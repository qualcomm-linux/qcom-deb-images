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
KERNEL_NAME=""
BUILD_ID=""

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
        --kernel-name)
            KERNEL_NAME="${2:?Missing value for --kernel-name}"
            shift 2
            ;;
        --build-id)
            BUILD_ID="${2:?Missing value for --build-id}"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

case "$INSTALLER" in
    cdrom|live) ;;
    *)
        echo "Unknown installer type: $INSTALLER (expected cdrom or live)" >&2
        exit 1
        ;;
esac

# Each installer type gets its own live-build tree, including its cache/,
# under build/$ARCH/$SUITE/, so different installer types can build in
# parallel. Suites don't share trees, so e.g. trixie and forky don't collide.
SUITE_DIR="$REPO_ROOT/build/$ARCH/$SUITE"
BUILD_DIR="$SUITE_DIR/$INSTALLER"
# live is cdrom plus a live system, so it gets cdrom's overlay too.
OVERLAY_DIRS=("$REPO_ROOT/overlays/common" "$REPO_ROOT/overlays/$ARCH")
if [ "$INSTALLER" = live ]; then
    OVERLAY_DIRS+=("$REPO_ROOT/overlays/cdrom")
fi
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
# For cdrom, the chroot's filesystem.squashfs is never built: build.sh marks
# the binary_chroot, binary_rootfs and binary_manifest stages as done, as the
# installer installs from the pool. live builds it, for the live system. So
# only cache the bootstrap.
#
# Both types use live-build's cdrom installer: its live installer would copy
# the live system to the target instead of installing from the pool, so
# live's installer stays the same as cdrom's.
#
# Boot via shim + Debian's signed GRUB. "enable" fails the build if shim is
# missing, where the default "auto" silently falls back to unsigned GRUB.
#
# Don't let lb installer add firmware packages to the pool (and /firmware/):
# by default it adds every package in the archive's Contents that ships
# anything under /usr/lib/firmware, e.g. indi-dsi (for one .hex file), which
# drags in libindi, libnova, cfitsio, fftw3, theora etc., plus
# firmware-linux-free, ath9k-htc, carl9170 etc. None of it is for this
# hardware: build.sh adds the SoC and wifi firmware packages
# lib/fetch-firmware.sh fetched to config/packages.binary/ instead.
#
# Those land in the pool's non-free-firmware component, so list it in the
# archive areas: the ISO's Release only has the parent archive areas'
# components, and apt on the target skips any component it doesn't list.
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

# Build a FAT32 disk image rather than an ISO, so the kernel needs no
# ISO 9660 support. cdrom-detect falls back to mounting disks and USB
# partitions as vfat, and the UEFI firmware boots the FAT partition.
lb config \
    "${MIRROR_ARGS[@]}" \
    --distribution "$SUITE" \
    --architectures "$ARCH" \
    --archive-areas "main non-free-firmware" \
    --parent-archive-areas "main non-free-firmware" \
    --linux-packages none \
    --debian-installer cdrom \
    --debian-installer-distribution "$SUITE" \
    --debian-installer-gui true \
    --firmware-binary false \
    --chroot-filesystem squashfs \
    --cache-stages "bootstrap" \
    --uefi-secure-boot enable \
    --binary-images hdd \
    --binary-filesystem fat32

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

    if [ -d "$overlay_dir/package-lists" ]; then
        mkdir -p "$BUILD_DIR/config/package-lists"
        cp -a "$overlay_dir/package-lists/." "$BUILD_DIR/config/package-lists/"
    fi

    # Preseeds are per installer type. For ISOs, live-build's config/preseed/
    # only ends up as /install/preseed.cfg on the ISO, which d-i ignores
    # without a file= boot argument. So replace-di-kernel.sh puts them in the
    # initrd as /preseed.cfg instead, which d-i always loads, before anything
    # else runs. (Not via config/includes.installer: live-build unpacks the
    # initrd without fakeroot, which can't mknod in a container.) live's
    # installer is cdrom's, so it uses cdrom's preseeds.
    if compgen -G "$overlay_dir/preseed/cdrom/*.cfg" >/dev/null; then
        cat "$overlay_dir/preseed/cdrom/"*.cfg >> "$PRESEED"
    fi

    # The live system's initrd needs the same initramfs-tools hooks as the
    # installed system (see postinst.d/10-adsp), e.g. to reset the ADSP
    # before mounting the root filesystem from USB. The live overlay's
    # 0050-initramfs hook creates the initrd after copying
    # config/includes.chroot/, and lb's chroot_hacks regenerates it.
    if [ "$INSTALLER" = live ] && [ -d "$overlay_dir/includes.binary/qli/initramfs-tools" ]; then
        mkdir -p "$BUILD_DIR/config/includes.chroot/etc/initramfs-tools"
        cp -a "$overlay_dir/includes.binary/qli/initramfs-tools/." \
            "$BUILD_DIR/config/includes.chroot/etc/initramfs-tools/"
    fi

    if [ -d "$overlay_dir/lib" ]; then
        cp -a "$overlay_dir/lib/." "$BUILD_DIR/config/"
    fi
}

# The preseed is appended to per overlay, so start afresh. As are the live
# system's initramfs-tools hooks and the overlays' package lists, which
# differ between installer types.
PRESEED="$BUILD_DIR/config/installer-preseed.cfg"
rm -f "$PRESEED"
rm -rf "$BUILD_DIR/config/includes.chroot/etc/initramfs-tools"
for list in "$REPO_ROOT"/overlays/*/package-lists/*; do
    rm -f "$BUILD_DIR/config/package-lists/$(basename "$list")"
done

for overlay_dir in "${OVERLAY_DIRS[@]}"; do
    apply_overlay "$overlay_dir"
done

# Add the kernel name, suite, installer type and build ID to the boot menu's
# title, e.g. "qcom-deb-images installer (qcom-laptops forky cdrom 123456789-1)";
# the kernel name and build ID are left out when not given.
# apply_overlay copies theme.txt afresh on every run, so this never stacks.
TITLE=""
if [ -n "$KERNEL_NAME" ]; then
    TITLE+="$KERNEL_NAME "
fi
TITLE+="$SUITE $INSTALLER"
if [ -n "$BUILD_ID" ]; then
    TITLE+=" $BUILD_ID"
fi
TITLE=" ($TITLE)"
THEME="$BUILD_DIR/config/bootloaders/grub-pc/live-theme/theme.txt"
if [ -f "$THEME" ]; then
    sed -i -E "s|^(title-text: \"[^\"]*)\"|\1$TITLE\"|" "$THEME"
fi

echo "Configured live-build in: $BUILD_DIR"
