#!/usr/bin/env bash
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

set -euo pipefail

ARCH=arm64
SUITE="trixie"
KERNEL_NAME=""
KERNEL_DEB=""
USAGE="Usage: $0 [--suite SUITE] [--kernel-name NAME] KERNEL.deb"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --suite)
            SUITE="${2:?Missing value for --suite}"
            shift 2
            ;;
        # Names the build directory, e.g. qcom-next; defaults to the
        # kernel's version.
        --kernel-name)
            KERNEL_NAME="${2:?Missing value for --kernel-name}"
            shift 2
            ;;
        # The kernel's linux-image package, with its modules and DTBs.
        *)
            if [ -n "$KERNEL_DEB" ]; then
                echo "$USAGE" >&2
                exit 1
            fi
            KERNEL_DEB=$(realpath "$1")
            shift
            ;;
    esac
done

if [ -z "$KERNEL_DEB" ]; then
    echo "$USAGE" >&2
    exit 1
fi

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

# The kernel's package and version.
KERNEL_PACKAGE=$(dpkg-deb -f "$KERNEL_DEB" Package)
case "$KERNEL_PACKAGE" in
    linux-image-*) ;;
    *)
        echo "$KERNEL_DEB is not a linux-image package" >&2
        exit 1
        ;;
esac
KVER="${KERNEL_PACKAGE#linux-image-}"

# Each kernel and suite has its own live-build tree in BUILD_DIR, so e.g.
# trixie and forky don't collide.
BUILD_DIR="$INSTALLER_DIR/build/$SUITE-${KERNEL_NAME:-$KVER}"

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

# The kernel arguments the Qualcomm machines need, from comment-stripped
# kernel/kernel-cmdline.
KERNEL_ARGS="$(grep -v '^#' "$INSTALLER_DIR/live-build/kernel/kernel-cmdline" | xargs)"

# live-build's rootfs stages are skipped, as the installer installs from
# the pool (see "Skip live-build's rootfs stages" below).
#
# The bootstrap is still cached, in the build tree: installer_debian-installer
# reuses its packages from cache/packages.bootstrap/ and fails without them.
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

# Only stable suites have backports. On trixie, the pool needs some
# packages from them: see the package lists of the kernel topic.
BACKPORTS_ARGS=()
if [ "$SUITE" = "trixie" ]; then
    BACKPORTS_ARGS=(--backports true)
fi

# Build an iso-hybrid image, which also boots from a USB stick. The kernel
# needs ISO 9660 support, see kernel-configs/installer.config.
#
# live-build's GRUB menu passes the kernel arguments to the installer:
# --bootappend-install ends up before the "---", so d-i doesn't give it to
# the target (the preseed's add-kernel-opts does).
lb config \
    "${MIRROR_ARGS[@]}" \
    "${BACKPORTS_ARGS[@]}" \
    --distribution "$SUITE" \
    --architectures "$ARCH" \
    --linux-packages none \
    --debian-installer cdrom \
    --debian-installer-distribution "$SUITE" \
    --debian-installer-gui true \
    --cache-stages "bootstrap" \
    --binary-images iso-hybrid \
    --bootappend-install "$KERNEL_ARGS"

# Copy tracked customizations from live-build/ on top of the generated
# config/.

# Copy a live-build/ file to the build tree, keeping its mode.
overlay() {
    mkdir -p "$(dirname "$BUILD_DIR/$2")"
    cp -a "$INSTALLER_DIR/live-build/$1" "$BUILD_DIR/$2"
}

# Installer defaults: the pool, the udebs and the tasks.
overlay defaults/hooks/0060-prune-pool.hook.binary \
    config/hooks/normal/0060-prune-pool.hook.binary
overlay defaults/package-lists/installer-pool.list.binary \
    config/package-lists/installer-pool.list.binary
overlay defaults/package-lists/standard-task.list.binary \
    config/package-lists/standard-task.list.binary

# The custom kernel, in the installer and the pool.
overlay kernel/package-lists/linux-base.list.binary \
    config/package-lists/linux-base.list.binary
overlay kernel/hooks/0050-replace-di-kernel.hook.binary \
    config/hooks/normal/0050-replace-di-kernel.hook.binary
if [ "$SUITE" = "trixie" ]; then
    overlay kernel/archives/linux-base.pref.chroot \
        config/archives/linux-base.pref.chroot
fi

overlay defaults/cdrom_udeb_include config/debian-cd/cdrom_udeb_include

# live-build's config/preseed/ only ends up as /install/preseed.cfg on the
# image, which d-i ignores without a file= boot argument. So
# replace-di-kernel.sh puts the preseed in the initrd as /preseed.cfg
# instead, which d-i always loads, before anything else runs. (Not via
# config/includes.installer: live-build unpacks the initrd without fakeroot,
# which can't mknod in a container.)
PRESEED="$BUILD_DIR/config/installer-preseed.cfg"
cat "$INSTALLER_DIR/live-build/defaults/preseed/no-kernel-modules.cfg" \
    > "$PRESEED"

stage "Prepare installer"

# Add the custom kernel to the pool, so base-installer installs it on the
# target instead of the stock one. lb installer copies the debs in
# config/packages.binary/ into the pool. They're named as apt would name them,
# which lb installer's *_$ARCH.deb and *_all.deb globs rely on. The
# 0060-prune-pool hook drops the stock kernel, keeping the debs listed
# in config/kernel-debs.list.
KERNEL_VERSION=$(dpkg-deb -f "$KERNEL_DEB" Version)
POOL_NAME="${KERNEL_PACKAGE}_${KERNEL_VERSION#*:}_$(dpkg-deb -f "$KERNEL_DEB" Architecture).deb"
install -D -m 0644 "$KERNEL_DEB" "$BUILD_DIR/config/packages.binary/$POOL_NAME"
echo "$POOL_NAME" > "$BUILD_DIR/config/kernel-debs.list"

stage "Build bootstrap and chroot"

lb bootstrap
lb chroot

stage "Build installer"

lb installer

stage "Unpack kernel"

# For 0050-replace-di-kernel.hook.binary, which runs during lb binary.
KERNEL_DIR="$BUILD_DIR/kernel"
rm -rf "$KERNEL_DIR"
mkdir -p "$KERNEL_DIR"
trap 'rm -rf "$KERNEL_DIR"' EXIT

echo "Unpacking $KERNEL_DEB"
dpkg-deb -x "$KERNEL_DEB" "$KERNEL_DIR"

stage "Configure installer kernel"

# The topics' preseeds are gathered into $PRESEED above. Add the custom
# kernel to it: base-installer would otherwise look for the stock kernel's
# package.
cat >> "$PRESEED" <<EOF
# Generated by build-installer.sh: install the custom kernel from the pool
# and boot it with the kernel arguments.
d-i base-installer/kernel/image string $KERNEL_PACKAGE
# The kernel arguments for the target's boot entries.
d-i debian-installer/add-kernel-opts string $KERNEL_ARGS
EOF

# 0050-replace-di-kernel.hook.binary replaces the installer kernel during
# lb binary, before the pool is pruned and the GRUB hooks run.
{
    printf 'INSTALLER_DIR=%q\n' "$INSTALLER_DIR"
    printf 'KERNEL_DIR=%q\n' "$KERNEL_DIR"
} > "$BUILD_DIR/config/replace-di-kernel.conf"

# live-build only ships udeb_include lists for amd64, and binary_disk fails
# for cdrom when ${ARCH}_udeb_include is missing. It leaves an existing
# binary/.disk/udeb_include alone, so pre-seed ours from the overlay.
install -D -m 0644 "$BUILD_DIR/config/debian-cd/cdrom_udeb_include" \
    "$BUILD_DIR/binary/.disk/udeb_include"

# Skip live-build's rootfs stages. binary_chroot copies the chroot and
# binary_rootfs runs mksquashfs on it, then binary_manifest lists its
# packages. None of it is used: the image only boots the installer, and
# d-i installs from the pool. Marking the stages done leaves the chroot as
# binary_rootfs would, and nothing else needs them. ("--chroot-filesystem
# none" can't do this: it replaces the whole binary/ tree with the chroot,
# wiping the installer.)
mkdir -p "$BUILD_DIR/.build"
touch \
    "$BUILD_DIR/.build/binary_chroot" \
    "$BUILD_DIR/.build/binary_rootfs" \
    "$BUILD_DIR/.build/binary_manifest"

stage "Build image"

lb binary

rm -rf "$KERNEL_DIR"
trap - EXIT

echo "=== Complete ==="

# Show the image path relative to where the script was run from.
cd "$CALLER_DIR"
ls -lh "$(realpath --relative-to=. "$BUILD_DIR")"/*.iso
