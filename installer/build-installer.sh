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

# The kernel's package and version. The version is also the flavour, which
# makes live-build look for chroot/boot/vmlinuz-$KVER for the live system.
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

# The image has a live GNOME system, from the chroot's filesystem.squashfs,
# and the installer, which installs the target from the pool.
#
# The bootstrap is cached, in the build tree: installer_debian-installer
# reuses its packages from cache/packages.bootstrap/ and fails without them.
#
# Use live-build's cdrom installer: its live installer would copy the live
# system to the target instead of installing from the pool.
#
# Don't let lb installer add firmware packages to the pool (and /firmware/):
# by default it adds every package in the archive's Contents that ships
# anything under /usr/lib/firmware, e.g. indi-dsi (for one .hex file), which
# drags in libindi, libnova, cfitsio, fftw3, theora etc., plus
# firmware-linux-free, ath9k-htc, carl9170 etc. None of it is for this
# hardware: the SoC and wifi firmware packages are listed in
# firmware/package-lists/firmware.list.binary instead.
#
# List non-free-firmware in the archive areas: the chroot's apt downloads
# them from it, and apt on the target skips any component the ISO's Release
# doesn't list, which only has the parent archive areas' components. (lb
# files them under non-free; firmware/hooks/0040-non-free-firmware.hook.binary
# moves them back.)
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
# packages from them: see the package lists of the kernel and dtb-loader
# topics.
BACKPORTS_ARGS=()
if [ "$SUITE" = "trixie" ]; then
    BACKPORTS_ARGS=(--backports true)
fi

# Build an iso-hybrid image, which also boots from a USB stick. The kernel
# needs ISO 9660 support, see kernel-configs/installer.config.
#
# live-build's GRUB menu passes the kernel arguments to the installer and
# the live system: --bootappend-install ends up before the "---", so d-i
# doesn't give it to the target (the preseed's add-kernel-opts does), and
# the failsafe entry, which lacks them, is dropped.
lb config \
    "${MIRROR_ARGS[@]}" \
    "${BACKPORTS_ARGS[@]}" \
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
    --binary-images iso-hybrid \
    --linux-flavours "$KVER" \
    --bootappend-install "$KERNEL_ARGS" \
    --bootappend-live "boot=live components quiet splash $KERNEL_ARGS" \
    --bootappend-live-failsafe none

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

# The firmware, in the pool for the target (see POOL_INCLUDE below) and in
# the live system.
overlay firmware/package-lists/firmware.list.binary \
    config/package-lists/firmware.list.binary
overlay firmware/hooks/0040-non-free-firmware.hook.binary \
    config/hooks/normal/0040-non-free-firmware.hook.binary
overlay firmware/package-lists/firmware.list.binary \
    config/package-lists/firmware.list.chroot
if [ "$SUITE" = "trixie" ]; then
    overlay firmware/archives/firmware.pref.chroot \
        config/archives/firmware.pref.chroot
fi
overlay defaults/cdrom_udeb_include config/debian-cd/cdrom_udeb_include

# The target's post-install step, which runs the postinst.d parts.
overlay postinst/postinst.sh config/includes.binary/qli/postinst.sh

# The DTB loader, on the stick and on the target.
overlay dtb-loader/package-lists/dtb-loader.list.binary \
    config/package-lists/dtb-loader.list.binary
overlay dtb-loader/hooks/0100-dtb-loader.hook.binary \
    config/hooks/normal/0100-dtb-loader.hook.binary
overlay dtb-loader/dtb-loader.postinst.sh \
    config/includes.binary/qli/postinst.d/dtb-loader.postinst.sh

# GNOME, in the pool for the installer to offer and in the live system.
overlay defaults/package-lists/gnome.list.binary \
    config/package-lists/gnome.list.binary
overlay live/package-lists/gnome.list.chroot \
    config/package-lists/gnome.list.chroot
overlay live/hooks/0050-initramfs.hook.chroot \
    config/hooks/normal/0050-initramfs.hook.chroot

# Calamares, in the live system, to install it to disk.
overlay live/package-lists/calamares.list.chroot \
    config/package-lists/calamares.list.chroot
overlay live/package-lists/calamares.list.binary \
    config/package-lists/calamares.list.binary
overlay live/hooks/0060-calamares.hook.chroot \
    config/hooks/normal/0060-calamares.hook.chroot
overlay live/calamares/qli-postinst.conf \
    config/includes.chroot/etc/calamares/modules/qli-postinst.conf

# Calamares doesn't give the target the kernel arguments (d-i does, through
# add-kernel-opts below), so give the live system, and so the target, a GRUB
# snippet with them.
GRUB_D="$BUILD_DIR/config/includes.chroot/etc/default/grub.d"
mkdir -p "$GRUB_D"
cat > "$GRUB_D/qcom-deb-images.cfg" <<EOF
# Generated by build-installer.sh from kernel/kernel-cmdline.
GRUB_CMDLINE_LINUX="\$GRUB_CMDLINE_LINUX $KERNEL_ARGS"
EOF

# live-build's config/preseed/ only ends up as /install/preseed.cfg on the
# image, which d-i ignores without a file= boot argument. So
# replace-di-kernel.sh puts the preseed in the initrd as /preseed.cfg
# instead, which d-i always loads, before anything else runs. (Not via
# config/includes.installer: live-build unpacks the initrd without fakeroot,
# which can't mknod in a container.)
PRESEED="$BUILD_DIR/config/installer-preseed.cfg"
cat "$INSTALLER_DIR/live-build/defaults/preseed/no-kernel-modules.cfg" \
    "$INSTALLER_DIR/live-build/defaults/preseed/network-mirror.cfg" \
    "$INSTALLER_DIR/live-build/defaults/preseed/no-root-login.cfg" \
    "$INSTALLER_DIR/live-build/defaults/preseed/no-swap-prompt.cfg" \
    "$INSTALLER_DIR/live-build/defaults/preseed/tasksel-gnome.cfg" \
    "$INSTALLER_DIR/live-build/defaults/preseed/non-free-firmware.cfg" \
    "$INSTALLER_DIR/live-build/postinst/preseed/postinst.cfg" \
    > "$PRESEED"

# Only stable suites have backports.
if [ "$SUITE" = "trixie" ]; then
    cat "$INSTALLER_DIR/live-build/defaults/preseed/backports.cfg" >> "$PRESEED"
fi

# The postinst.d parts install files no package ships from the dtb-loader
# rootfs overlay, so ship it on the image as /qli/overlays/dtb-loader/, laid
# out like the target filesystem.
mkdir -p "$BUILD_DIR/config/includes.binary/qli/overlays/dtb-loader"
cp -a "$INSTALLER_DIR/../debos-recipes/overlays/dtb-loader/." \
    "$BUILD_DIR/config/includes.binary/qli/overlays/dtb-loader/"

stage "Prepare installer"

# live-build's udeb_exclude (copied from debian-cd) drops
# apt-mirror-setup, which asks "use a network mirror?", so the installer
# never sets up a mirror and tasksel only sees the ISO's pool. lb installer
# only builds its exclude list when ./exclude doesn't exist yet (and removes
# it afterwards), and config/debian-installer/udeb_exclude can only add to
# it. So pre-seed the same list without apt-mirror-setup.
LB_UDEB_DATA="/usr/share/live/build/data/debian-cd/$SUITE"
if [ -d "$LB_UDEB_DATA" ]; then
    cat "$LB_UDEB_DATA/udeb_exclude" \
        "$LB_UDEB_DATA/exclude-udebs" \
        "$LB_UDEB_DATA/exclude-udebs-$ARCH" 2>/dev/null |
        grep -v -e '^#' -e '^apt-mirror-setup$' > exclude || true
fi

# Add the custom kernel to the pool, so base-installer installs it on the
# target instead of the stock one. lb installer copies the debs in
# config/packages.binary/ into the pool. They're named as apt would name them,
# which lb installer's *_$ARCH.deb and *_all.deb globs rely on. The
# 0060-prune-pool hook drops the stock kernel, keeping the debs listed
# in config/kernel-debs.list.
#
# Also install the kernel in the live system: lb chroot installs every deb
# in config/packages.chroot/.
KERNEL_VERSION=$(dpkg-deb -f "$KERNEL_DEB" Version)
POOL_NAME="${KERNEL_PACKAGE}_${KERNEL_VERSION#*:}_$(dpkg-deb -f "$KERNEL_DEB" Architecture).deb"
install -D -m 0644 "$KERNEL_DEB" "$BUILD_DIR/config/packages.binary/$POOL_NAME"
install -D -m 0644 "$KERNEL_DEB" "$BUILD_DIR/config/packages.chroot/$POOL_NAME"
echo "$POOL_NAME" > "$BUILD_DIR/config/kernel-debs.list"

# Packages from the pool for pkgsel to install on the target, on top of
# the base system and the standard task.
POOL_INCLUDE=()

# Add the packages of a list in config/package-lists/ to POOL_INCLUDE, so
# they're only listed once. Comments are dropped.
pool_include() {
    local pkgs
    mapfile -t pkgs < <(sed 's/#.*//' "$BUILD_DIR/config/package-lists/$1" |
        xargs -r -n1)
    POOL_INCLUDE+=("${pkgs[@]}")
}

# The firmware, which lb binary adds to the pool's non-free-firmware
# component. lb installer adds none of it (see the lb config call above).
pool_include firmware.list.binary

# The DTB loader, which lb binary adds to the pool with its dependencies.
pool_include dtb-loader.list.binary

stage "Build bootstrap and chroot"

# After the kernel is added: lb chroot installs config/packages.chroot/.
lb bootstrap
lb chroot

# --linux-packages none makes binary_linux-image skip the live system's
# kernel and initrd, so put them in binary/live/ via config/includes.binary/.
# The 0100-dtb-loader binary hook wraps the kernel with Stubble.
LIVE_BOOT="$BUILD_DIR/config/includes.binary/live"
mkdir -p "$LIVE_BOOT"
cp -L chroot/boot/vmlinuz-* chroot/boot/initrd.img-* "$LIVE_BOOT/"

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
# pkgsel/include is a single setting, so list all of the packages at once.
cat >> "$PRESEED" <<EOF
# Generated by build-installer.sh: install these packages from the pool.
d-i pkgsel/include string ${POOL_INCLUDE[*]}
EOF

# 0050-replace-di-kernel.hook.binary replaces the installer kernel during
# lb binary, before the pool is pruned and the GRUB hooks run.
{
    printf 'INSTALLER_DIR=%q\n' "$INSTALLER_DIR"
    printf 'KERNEL_DIR=%q\n' "$KERNEL_DIR"
} > "$BUILD_DIR/config/replace-di-kernel.conf"

stage "Extract DTBs"

# All of the kernel's DTBs, not just the ones the ISO ships: the
# 0100-dtb-loader binary hook bundles only those with a Stubble HWID
# entry into the Stubble EFI binary. The rest stay in config/.
mkdir -p "$BUILD_DIR/config/dtbs"

# The kernel package ships them in /usr/lib/linux-image-$KVER/.
find "$KERNEL_DIR/usr/lib/linux-image-$KVER" -name '*.dtb' -type f \
    -exec cp -t "$BUILD_DIR/config/dtbs/" {} +

if ! compgen -G "$BUILD_DIR/config/dtbs/*.dtb" >/dev/null; then
    echo "Kernel package contains no DTBs" >&2
    exit 1
fi

# live-build only ships udeb_include lists for amd64, and binary_disk fails
# for cdrom when ${ARCH}_udeb_include is missing. It leaves an existing
# binary/.disk/udeb_include alone, so pre-seed ours from the overlay.
install -D -m 0644 "$BUILD_DIR/config/debian-cd/cdrom_udeb_include" \
    "$BUILD_DIR/binary/.disk/udeb_include"

stage "Build image"

lb binary

rm -rf "$KERNEL_DIR"
trap - EXIT

echo "=== Complete ==="

# Show the image path relative to where the script was run from.
cd "$CALLER_DIR"
ls -lh "$(realpath --relative-to=. "$BUILD_DIR")"/*.iso
