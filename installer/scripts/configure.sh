#!/usr/bin/env bash
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

set -euo pipefail

# Generate the live-build configuration for one installer type and suite in
# build/$ARCH/$SUITE/$INSTALLER/, then copy the overlays on top of it. Called
# by build.sh; run from anywhere.
REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

ARCH=arm64
INSTALLER="cdrom"
SUITE="trixie"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --arch)
            ARCH="${2:?Missing value for --arch}"
            shift 2
            ;;
        --installer)
            INSTALLER="${2:?Missing value for --installer}"
            shift 2
            ;;
        --suite)
            SUITE="${2:?Missing value for --suite}"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

case "$INSTALLER" in
    cdrom) ;;
    *)
        echo "Unknown installer type: $INSTALLER (expected cdrom)" >&2
        exit 1
        ;;
esac

# Each installer type gets its own live-build tree, including its cache/,
# under build/$ARCH/$SUITE/, so different installer types can build in
# parallel. Suites don't share trees, so e.g. trixie and forky don't collide.
SUITE_DIR="$REPO_ROOT/build/$ARCH/$SUITE"
BUILD_DIR="$SUITE_DIR/$INSTALLER"
OVERLAY_DIRS=("$REPO_ROOT/overlays/common" "$REPO_ROOT/overlays/$ARCH")
OVERLAY_DIRS+=("$REPO_ROOT/overlays/$INSTALLER")

if ! command -v lb >/dev/null 2>&1; then
    echo 'Error: live-build (lb) is not installed' >&2
    exit 1
fi

mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

mkdir -p cache

# lb config updates the generated config/ in-place. For a truly fresh
# configuration, start with an empty build directory (see docs/installer.md).
# It re-reads the saved config/ before applying these arguments, so dropping
# an option here does NOT reset it to the default: set it explicitly instead.
#
# The chroot's filesystem.squashfs is never built: build.sh marks the
# binary_chroot, binary_rootfs and binary_manifest stages as done, as the
# installer installs from the pool. So only cache the bootstrap.
#
#
# Boot via shim + Debian's signed GRUB. "enable" fails the build if shim is
# missing, where the default "auto" silently falls back to unsigned GRUB.
#
# Set the mirrors explicitly, and the parent mirrors too as lb saves them
# separately and doesn't rederive them. Keep the trailing slashes: lb appends
# "dists" to the debian-installer mirror directly.
MIRROR="http://deb.debian.org/debian/"
MIRROR_SECURITY="http://security.debian.org/"
MIRROR_ARGS=()
for mirror in bootstrap chroot debian-installer; do
    MIRROR_ARGS+=(--mirror-"$mirror" "$MIRROR" --parent-mirror-"$mirror" "$MIRROR")
done
MIRROR_ARGS+=(--mirror-chroot-security "$MIRROR_SECURITY"
    --parent-mirror-chroot-security "$MIRROR_SECURITY")

lb config \
    "${MIRROR_ARGS[@]}" \
    --distribution "$SUITE" \
    --architectures "$ARCH" \
    --linux-packages none \
    --debian-installer cdrom \
    --debian-installer-distribution "$SUITE" \
    --debian-installer-gui true \
    --chroot-filesystem squashfs \
    --cache-stages "bootstrap" \
    --uefi-secure-boot enable

# Copy tracked customizations (hooks, includes, bootloader templates, shared
# libs) on top of the generated config/: first overlays/common/, then the
# arch-specific overlay, then the installer type's, so the later ones win.
# lb config never touches or removes existing files under config/hooks/ or
# config/includes.binary/, so this is safe to rerun alongside `lb config` on
# every invocation.
apply_overlay() {
    local overlay_dir="$1"

    [ -d "$overlay_dir" ] || return 0

    echo "Applying overlay: $overlay_dir"

    if [ -d "$overlay_dir/hooks" ]; then
        mkdir -p "$BUILD_DIR/config/hooks"
        cp -a "$overlay_dir/hooks/." "$BUILD_DIR/config/hooks/"
    fi

    if [ -d "$overlay_dir/includes.binary" ]; then
        mkdir -p "$BUILD_DIR/config/includes.binary"
        cp -a "$overlay_dir/includes.binary/." "$BUILD_DIR/config/includes.binary/"
    fi

    if [ -d "$overlay_dir/bootloaders" ]; then
        mkdir -p "$BUILD_DIR/config/bootloaders"
        cp -a "$overlay_dir/bootloaders/." "$BUILD_DIR/config/bootloaders/"
    fi

    if [ -d "$overlay_dir/debian-cd" ]; then
        mkdir -p "$BUILD_DIR/config/debian-cd"
        cp -a "$overlay_dir/debian-cd/." "$BUILD_DIR/config/debian-cd/"
    fi
}

for overlay_dir in "${OVERLAY_DIRS[@]}"; do
    apply_overlay "$overlay_dir"
done

echo "Configured live-build in: $BUILD_DIR"
