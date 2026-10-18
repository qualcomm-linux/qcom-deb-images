#!/usr/bin/env bash
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

set -euo pipefail

ARCH=arm64
INSTALLER="cdrom"
SUITE="trixie"
USAGE="Usage: $0 [--arch amd64|arm64] [--installer cdrom] [--suite SUITE]"

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
        echo "$USAGE" >&2
        exit 1
        ;;
esac

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this script as root (sudo $0 ...)" >&2
    exit 1
fi

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(realpath "$SCRIPT_DIR/..")
# Each installer type has its own live-build tree (including cache/ and
# firmware/) in BUILD_DIR, so different types can build in parallel.
SUITE_DIR="$REPO_ROOT/build/$ARCH/$SUITE"
BUILD_DIR="$SUITE_DIR/$INSTALLER"

mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

# Print a stage's name.
stage() {
    echo "=== $1 ==="
}

stage "Configure live-build"

CONFIGURE_ARGS=(--arch "$ARCH" --suite "$SUITE" --installer "$INSTALLER")
"$SCRIPT_DIR/configure.sh" "${CONFIGURE_ARGS[@]}"

stage "Clean previous binary build"

lb clean --binary

# live-build only ships udeb_include lists for amd64, and binary_disk fails
# for cdrom when ${ARCH}_udeb_include is missing. It leaves an existing
# binary/.disk/udeb_include alone, so pre-seed ours from the overlay.
UDEB_INCLUDE="$BUILD_DIR/config/debian-cd/cdrom_udeb_include"
if [ -f "$UDEB_INCLUDE" ]; then
    install -D -m 0644 "$UDEB_INCLUDE" "$BUILD_DIR/binary/.disk/udeb_include"
fi

# Skip live-build's rootfs stages. binary_chroot copies the chroot and
# binary_rootfs runs mksquashfs on it (under qemu-user for a foreign arch),
# then binary_manifest lists its packages. None of it is used: the ISO only
# boots the installer, and d-i installs from the pool. Marking the stages
# done leaves the chroot as binary_rootfs would, and nothing else needs
# them. ("--chroot-filesystem none" can't do this: it replaces the whole
mkdir -p "$BUILD_DIR/.build"
touch \
    "$BUILD_DIR/.build/binary_chroot" \
    "$BUILD_DIR/.build/binary_rootfs" \
    "$BUILD_DIR/.build/binary_manifest"

stage "Build image"

lb binary

echo "=== Complete ==="

ls -lh ./*.iso
