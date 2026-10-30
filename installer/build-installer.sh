#!/usr/bin/env bash
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

set -euo pipefail

ARCH=arm64
SUITE="trixie"
USAGE="Usage: $0 [--arch arm64] [--suite SUITE]"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --arch)
            ARCH="${2:?Missing value for --arch}"
            shift 2
            ;;
        --suite)
            SUITE="${2:?Missing value for --suite}"
            shift 2
            ;;
        *)
            echo "$USAGE" >&2
            exit 1
            ;;
    esac
done

case "$ARCH" in
    arm64) ;;
    *)
        echo "$USAGE" >&2
        exit 1
        ;;
esac

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this script as root (sudo $0 ...)" >&2
    exit 1
fi

if ! command -v lb >/dev/null 2>&1; then
    echo 'Error: live-build (lb) is not installed' >&2
    exit 1
fi

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
INSTALLER_DIR=$SCRIPT_DIR
# Each suite has its own live-build tree in BUILD_DIR, so e.g. trixie and
# forky don't collide.
BUILD_DIR="$INSTALLER_DIR/build/$ARCH/$SUITE"

# The build only runs once in a tree, so refuse to reuse one.
if [ -e "$BUILD_DIR" ]; then
    echo "$BUILD_DIR already exists, remove it first (make clean-installer)" >&2
    exit 1
fi

mkdir -p "$(dirname "$BUILD_DIR")"
mkdir "$BUILD_DIR"
CALLER_DIR=$PWD
cd "$BUILD_DIR"

# Print a stage's name.
stage() {
    echo "=== $1 ==="
}

stage "Configure live-build"

# live-build's rootfs stages are skipped, as the installer installs from
# the pool (see "Skip live-build's rootfs stages" below).
#
# The bootstrap is still cached, in the build tree: installer_debian-installer
# reuses its packages from cache/packages.bootstrap/ and fails without them.
#
# Don't boot via shim, as the image isn't signed: it boots Debian's signed
# GRUB directly (0099-grub-efi-install.hook.binary).
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
    --linux-packages none \
    --debian-installer cdrom \
    --debian-installer-distribution "$SUITE" \
    --debian-installer-gui true \
    --cache-stages "bootstrap" \
    --uefi-secure-boot disable \
    --binary-images hdd \
    --binary-filesystem fat32

# Copy tracked customizations from live-build/ on top of the generated
# config/, and the files to add to the installer's initrds to
# initrd-overlay/.

# Copy a live-build/ file to the build tree, keeping its mode.
overlay() {
    mkdir -p "$(dirname "$BUILD_DIR/$2")"
    cp -a "$INSTALLER_DIR/live-build/$1" "$BUILD_DIR/$2"
}

overlay defaults/cdrom_udeb_include config/debian-cd/cdrom_udeb_include

# The stick's and the target's bootloader.
overlay bootloader/hooks/0099-grub-efi-install.hook.binary \
    config/hooks/normal/0099-grub-efi-install.hook.binary
overlay bootloader/initrd/usr/lib/post-base-installer.d/99disable-shim \
    initrd-overlay/usr/lib/post-base-installer.d/99disable-shim

stage "Build bootstrap and chroot"

lb bootstrap
lb chroot

stage "Build installer"

lb installer

# live-build only ships udeb_include lists for amd64, and binary_disk fails
# for cdrom when ${ARCH}_udeb_include is missing. It leaves an existing
# binary/.disk/udeb_include alone, so pre-seed ours from the overlay.
install -D -m 0644 "$BUILD_DIR/config/debian-cd/cdrom_udeb_include" \
    "$BUILD_DIR/binary/.disk/udeb_include"

# Skip live-build's rootfs stages. binary_chroot copies the chroot and
# binary_rootfs runs mksquashfs on it (under qemu-user for a foreign arch),
# then binary_manifest lists its packages. None of it is used: the ISO only
# boots the installer, and d-i installs from the pool. Marking the stages
# done leaves the chroot as binary_rootfs would, and nothing else needs
# them. ("--chroot-filesystem none" can't do this: it replaces the whole
# binary/ tree with the chroot, wiping the installer.)
mkdir -p "$BUILD_DIR/.build"
touch \
    "$BUILD_DIR/.build/binary_chroot" \
    "$BUILD_DIR/.build/binary_rootfs" \
    "$BUILD_DIR/.build/binary_manifest"

stage "Build image"

lb binary

echo "=== Complete ==="

# Show the image path relative to where the script was run from.
cd "$CALLER_DIR"
ls -lh "$(realpath --relative-to=. "$BUILD_DIR")"/*.img
