#!/usr/bin/env bash
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

set -euo pipefail

ARCH=arm64
VARIANT="gnome"
SUITE="trixie"
KERNEL_NAME=""
BUILD_ID=""
KERNEL_DEBS=()
USAGE="Usage: $0 [--arch arm64] [--variant console|xfce|gnome] [--suite SUITE] [--kernel-name NAME] [--build-id ID] KERNEL.deb..."

while [ "$#" -gt 0 ]; do
    case "$1" in
        --arch)
            ARCH="${2:?Missing value for --arch}"
            shift 2
            ;;
        --variant)
            VARIANT="${2:?Missing value for --variant}"
            shift 2
            ;;
        --suite)
            SUITE="${2:?Missing value for --suite}"
            shift 2
            ;;
        # Shown in the boot menu's title, e.g. qcom-laptops and the CI run.
        --kernel-name)
            KERNEL_NAME="${2:?Missing value for --kernel-name}"
            shift 2
            ;;
        --build-id)
            BUILD_ID="${2:?Missing value for --build-id}"
            shift 2
            ;;
        # The kernel package, or all of its packages if it is split (like
        # Debian's linux-image, linux-base, linux-binary and linux-modules).
        *)
            KERNEL_DEBS+=("$(realpath "$1")")
            shift
            ;;
    esac
done

if [ "${#KERNEL_DEBS[@]}" -eq 0 ]; then
    echo "$USAGE" >&2
    exit 1
fi

case "$ARCH" in
    arm64) ;;
    *)
        echo "$USAGE" >&2
        exit 1
        ;;
esac

# The variants match the rootfs recipes'. All of them install the base
# system from the pool on the ISO, with the desktop's task in the pool
# too (so it can be installed offline) and ticked by default. console has
# no desktop in the pool and ticks none, but tasksel can still fetch one
# from the network mirror.
DESKTOP=""
case "$VARIANT" in
    console) ;;
    xfce|gnome) DESKTOP="$VARIANT" ;;
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
# Each variant has its own live-build tree (including firmware/) in
# BUILD_DIR under build/$ARCH/$SUITE/, so different variants can build in
# parallel. Suites don't share trees, so e.g. trixie and forky don't
# collide.
SUITE_DIR="$INSTALLER_DIR/build/$ARCH/$SUITE"
BUILD_DIR="$SUITE_DIR/$VARIANT"

# The build only runs once in a tree, so refuse to reuse one.
if [ -e "$BUILD_DIR" ]; then
    echo "$BUILD_DIR already exists, remove it first (make clean-installer)" >&2
    exit 1
fi

mkdir -p "$SUITE_DIR"
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

# Build a FAT32 disk image rather than an ISO, so the kernel needs no
# ISO 9660 support. cdrom-detect falls back to mounting disks and USB
# partitions as vfat, and the UEFI firmware boots the FAT partition.
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

# The boot menu's kernel arguments.
overlay kernel/kernel-cmdline config/kernel-cmdline

# Installer defaults: the pool, the udebs and the tasks.
overlay defaults/hooks/0060-prune-pool.hook.binary \
    config/hooks/normal/0060-prune-pool.hook.binary
overlay defaults/package-lists/installer-pool.list.binary \
    config/package-lists/installer-pool.list.binary
overlay defaults/package-lists/standard-task.list.binary \
    config/package-lists/standard-task.list.binary
overlay kernel/package-lists/linux-base.list.binary \
    config/package-lists/linux-base.list.binary
overlay kernel/hooks/0050-replace-di-kernel.hook.binary \
    config/hooks/normal/0050-replace-di-kernel.hook.binary
if [ "$SUITE" = "trixie" ]; then
    overlay kernel/archives/linux-base.pref.chroot \
        config/archives/linux-base.pref.chroot
fi

# The firmware, in the pool for the target (see POOL_INCLUDE below) and the
# installer initrds.
overlay firmware/package-lists/firmware.list.binary \
    config/package-lists/firmware.list.binary
overlay firmware/hooks/0040-non-free-firmware.hook.binary \
    config/hooks/normal/0040-non-free-firmware.hook.binary
if [ "$SUITE" = "trixie" ]; then
    overlay firmware/archives/firmware.pref.chroot \
        config/archives/firmware.pref.chroot
fi
overlay defaults/cdrom_udeb_include config/debian-cd/cdrom_udeb_include

# The target's post-install step, which runs the postinst.d parts.
overlay postinst/postinst.sh config/includes.binary/qli/postinst.sh

# The stick's and the target's bootloader.
overlay bootloader/grub/theme.txt \
    config/bootloaders/grub-pc/live-theme/theme.txt
overlay bootloader/grub/bg.png config/bootloaders/grub-pc/live-theme/bg.png
overlay bootloader/hooks/0099-grub-efi-install.hook.binary \
    config/hooks/normal/0099-grub-efi-install.hook.binary
overlay bootloader/hooks/0110-grub-menu.hook.binary \
    config/hooks/normal/0110-grub-menu.hook.binary
overlay bootloader/initrd/usr/lib/post-base-installer.d/99disable-shim \
    initrd-overlay/usr/lib/post-base-installer.d/99disable-shim

# The ADSP reset, in the installer and on the target.
overlay qcom-adsp/qcom-adsp.postinst.sh \
    config/includes.binary/qli/postinst.d/qcom-adsp.postinst.sh
overlay qcom-adsp/initrd/usr/lib/debian-installer-startup.d/S03qcom-adsp \
    initrd-overlay/usr/lib/debian-installer-startup.d/S03qcom-adsp
overlay qcom-adsp/initrd/usr/lib/modprobe.d/qcom-adsp.conf \
    initrd-overlay/usr/lib/modprobe.d/qcom-adsp.conf

# The DTB loader, on the stick and on the target.
overlay dtb-loader/package-lists/dtb-loader.list.binary \
    config/package-lists/dtb-loader.list.binary
overlay dtb-loader/hooks/0100-dtb-loader.hook.binary \
    config/hooks/normal/0100-dtb-loader.hook.binary
overlay dtb-loader/dtb-loader.postinst.sh \
    config/includes.binary/qli/postinst.d/dtb-loader.postinst.sh

# The desktop task is only offered when the pool has it.
if [ -n "$DESKTOP" ]; then
    overlay "defaults/package-lists/$DESKTOP.list.binary" \
        "config/package-lists/$DESKTOP.list.binary"
fi

# For ISOs, live-build's config/preseed/ only ends up as /install/preseed.cfg
# on the ISO, which d-i ignores without a file= boot argument. So
# replace-di-kernel.sh puts the preseed in the initrd as /preseed.cfg
# instead, which d-i always loads, before anything else runs. (Not via
# config/includes.installer: live-build unpacks the initrd without fakeroot,
# which can't mknod in a container.) All of the variants use the same
# preseeds, except for the desktop tasksel ticks by default.
PRESEED="$BUILD_DIR/config/installer-preseed.cfg"
cat "$INSTALLER_DIR/live-build/defaults/preseed/no-kernel-modules.cfg" \
    "$INSTALLER_DIR/live-build/defaults/preseed/network-mirror.cfg" \
    "$INSTALLER_DIR/live-build/defaults/preseed/no-root-login.cfg" \
    "$INSTALLER_DIR/live-build/defaults/preseed/no-swap-prompt.cfg" \
    "$INSTALLER_DIR/live-build/defaults/preseed/tasksel-${DESKTOP:-console}.cfg" \
    "$INSTALLER_DIR/live-build/defaults/preseed/non-free-firmware.cfg" \
    "$INSTALLER_DIR/live-build/postinst/preseed/postinst.cfg" \
    > "$PRESEED"

# Only stable suites have backports.
case "$SUITE" in
    trixie)
        cat "$INSTALLER_DIR/live-build/defaults/preseed/backports.cfg" >> "$PRESEED"
        ;;
esac

# The postinst.d parts install files no package ships from these
# rootfs overlays, so ship them on the image as /qli/overlays/<name>/, laid
# out like the target filesystem.
DEBOS_OVERLAYS="$INSTALLER_DIR/../debos-recipes/overlays"
for overlay in dtb-loader qcom-adsp; do
    mkdir -p "$BUILD_DIR/config/includes.binary/qli/overlays/$overlay"
    cp -a "$DEBOS_OVERLAYS/$overlay/." \
        "$BUILD_DIR/config/includes.binary/qli/overlays/$overlay/"
done

# Add the kernel name, suite, variant and build ID to the boot menu's
# title, e.g. "qcom-deb-images installer (qcom-laptops forky gnome 123456789-1)";
# the kernel name and build ID are left out when not given.
TITLE=""
if [ -n "$KERNEL_NAME" ]; then
    TITLE+="$KERNEL_NAME "
fi
TITLE+="$SUITE $VARIANT"
if [ -n "$BUILD_ID" ]; then
    TITLE+=" $BUILD_ID"
fi
TITLE=" ($TITLE)"
THEME="$BUILD_DIR/config/bootloaders/grub-pc/live-theme/theme.txt"
sed -i -E "s|^(title-text: \"[^\"]*)\"|\1$TITLE\"|" "$THEME"

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
PACKAGES_BINARY="$BUILD_DIR/config/packages.binary"
KERNEL_DEBS_LIST="$BUILD_DIR/config/kernel-debs.list"
mkdir -p "$PACKAGES_BINARY"
: > "$KERNEL_DEBS_LIST"

# Copy a deb into config/packages.binary/, named as apt would name it.
# Sets POOL_PKG and POOL_NAME to its package and file names.
pool_add() {
    local ver
    POOL_PKG=$(dpkg-deb -f "$1" Package)
    ver=$(dpkg-deb -f "$1" Version)
    POOL_NAME="${POOL_PKG}_${ver#*:}_$(dpkg-deb -f "$1" Architecture).deb"
    cp "$1" "$PACKAGES_BINARY/$POOL_NAME"
}

KERNEL_PACKAGE=""
for DEB in "${KERNEL_DEBS[@]}"; do
    pool_add "$DEB"
    echo "$POOL_NAME" >> "$KERNEL_DEBS_LIST"
    case "$POOL_PKG" in
        linux-image-*)
            if [ -n "$KERNEL_PACKAGE" ]; then
                echo "More than one linux-image package: $KERNEL_PACKAGE, $POOL_PKG" >&2
                exit 1
            fi
            KERNEL_PACKAGE="$POOL_PKG"
            ;;
    esac
done

if [ -z "$KERNEL_PACKAGE" ]; then
    echo "No linux-image package among the kernel packages" >&2
    exit 1
fi

# Packages from the pool for pkgsel to install on the target, on top of
# the base system and the standard task.
POOL_INCLUDE=()

# Add the packages of a list in config/package-lists/ to POOL_INCLUDE, so
# they're only listed once. Comments are dropped, #if and #endif included,
# so the list's conditions must hold for every variant.
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

for DEB in "${KERNEL_DEBS[@]}"; do
    echo "Unpacking $DEB"
    dpkg-deb -x "$DEB" "$KERNEL_DIR"
done

stage "Configure installer kernel"

# The topics' preseeds are gathered into $PRESEED above. Add the custom
# kernel to it: base-installer would otherwise look for the stock kernel's
# package.
cat >> "$PRESEED" <<EOF
# Generated by build-installer.sh: install the custom kernel from the pool.
d-i base-installer/kernel/image string $KERNEL_PACKAGE
EOF
# pkgsel/include is a single setting, so list all of the packages at once.
if [ "${#POOL_INCLUDE[@]}" -gt 0 ]; then
    cat >> "$PRESEED" <<EOF
# Generated by build-installer.sh: install these packages from the pool.
d-i pkgsel/include string ${POOL_INCLUDE[*]}
EOF
fi

# 0050-replace-di-kernel.hook.binary replaces the installer kernel during
# lb binary, once the firmware is in the pool: lb installer doesn't
# download the packages of *.list.binary, binary_package-lists does.
{
    printf 'INSTALLER_DIR=%q\n' "$INSTALLER_DIR"
    printf 'KERNEL_DIR=%q\n' "$KERNEL_DIR"
} > "$BUILD_DIR/config/replace-di-kernel.conf"

stage "Extract DTBs"

# All of the kernel's DTBs, not just the ones the ISO ships: the
# 0100-dtb-loader binary hook bundles only those with a Stubble HWID
# entry into the Stubble EFI binary. The rest stay in config/.
mkdir -p "$BUILD_DIR/config/dtbs"

# Single kernel packages ship the DTBs in /usr/lib/linux-image-$KVER/;
# Debian's split ones in /usr/lib/modules/$KVER/dtb/, with the former as
# a symlink to it.
find "$KERNEL_DIR" \
    \( -path '*/linux-image-*/*' -o -path '*/modules/*/dtb/*' \) \
    -name '*.dtb' -type f \
    -exec cp -t "$BUILD_DIR/config/dtbs/" {} +

if ! compgen -G "$BUILD_DIR/config/dtbs/*.dtb" >/dev/null; then
    echo "Kernel package(s) contain no DTBs" >&2
    exit 1
fi

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

rm -rf "$KERNEL_DIR"
trap - EXIT

echo "=== Complete ==="

# Show the image path relative to where the script was run from.
cd "$CALLER_DIR"
ls -lh "$(realpath --relative-to=. "$BUILD_DIR")"/*.img
