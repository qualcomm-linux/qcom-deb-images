#!/usr/bin/env bash
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

set -euo pipefail

ARCH=arm64
INSTALLER="cdrom"
SUITE="trixie"
KERNEL_NAME=""
BUILD_ID=""
KERNEL_DEBS=()
USAGE="Usage: $0 [--arch amd64|arm64] [--installer cdrom|live|netinst] [--suite SUITE] [--kernel-name NAME] [--build-id ID] KERNEL.deb..."

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

# cdrom installs the base system from the pool on the ISO. live adds a live
# system to the boot menu, with the same installer. netinst is cdrom without
# the desktops in the pool.
case "$INSTALLER" in
    cdrom|live|netinst) ;;
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
if [ -n "$KERNEL_NAME" ]; then
    CONFIGURE_ARGS+=(--kernel-name "$KERNEL_NAME")
fi
if [ -n "$BUILD_ID" ]; then
    CONFIGURE_ARGS+=(--build-id "$BUILD_ID")
fi
"$SCRIPT_DIR/configure.sh" "${CONFIGURE_ARGS[@]}"

stage "Clean previous binary build"

lb clean --binary

stage "Regenerate installer"

rm -f \
    .build/installer_debian-installer \
    .build/installer_preseed

# live-build's udeb_exclude (copied from debian-cd) drops
# apt-mirror-setup, which asks "use a network mirror?", so the installer
# never sets up a mirror and tasksel only sees the ISO's pool. lb installer
# only builds its exclude list when ./exclude doesn't exist yet (and removes
# it afterwards), and config/debian-installer/udeb_exclude can only add to
# it. So pre-seed the same list without apt-mirror-setup.
LB_UDEB_DATA="/usr/share/live/build/data/debian-cd/$SUITE"
rm -f exclude
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
# For live, also install the kernel, its dependencies and the firmware in the
# live system: lb chroot installs every deb in config/packages.chroot/.
PACKAGES_BINARY="$BUILD_DIR/config/packages.binary"
PACKAGES_CHROOT="$BUILD_DIR/config/packages.chroot"
KERNEL_DEBS_LIST="$BUILD_DIR/config/kernel-debs.list"
rm -rf "$PACKAGES_BINARY" "$PACKAGES_CHROOT"
mkdir -p "$PACKAGES_BINARY"
: > "$KERNEL_DEBS_LIST"

# Copy a deb into config/packages.binary/, named as apt would name it, and
# with --chroot into config/packages.chroot/ too for live. Sets POOL_PKG and
# POOL_NAME to its package and file names.
pool_add() {
    local chroot=0 ver
    if [ "$1" = --chroot ]; then
        chroot=1
        shift
    fi
    POOL_PKG=$(dpkg-deb -f "$1" Package)
    ver=$(dpkg-deb -f "$1" Version)
    POOL_NAME="${POOL_PKG}_${ver#*:}_$(dpkg-deb -f "$1" Architecture).deb"
    cp "$1" "$PACKAGES_BINARY/$POOL_NAME"
    if [ "$chroot" = 1 ] && [ "$INSTALLER" = live ]; then
        install -D -m 0644 "$1" "$PACKAGES_CHROOT/$POOL_NAME"
    fi
}

KERNEL_PACKAGE=""
for DEB in "${KERNEL_DEBS[@]}"; do
    pool_add --chroot "$DEB"
    echo "$POOL_NAME" >> "$KERNEL_DEBS_LIST"
    PKG="$POOL_PKG"
    case "$PKG" in
        linux-image-*)
            if [ -n "$KERNEL_PACKAGE" ]; then
                echo "More than one linux-image package: $KERNEL_PACKAGE, $PKG" >&2
                exit 1
            fi
            KERNEL_PACKAGE="$PKG"
            ;;
    esac
done

if [ -z "$KERNEL_PACKAGE" ]; then
    echo "No linux-image package among the kernel packages" >&2
    exit 1
fi

stage "Fetch kernel dependencies"

# base-installer installs the kernel from the pool, with no backports, so
# the pool needs anything newer the kernel depends on, e.g. linux-base from
# trixie-backports.
"$SCRIPT_DIR/lib/fetch-kernel-deps.sh" "$BUILD_DIR" "$SUITE" "$ARCH" "${KERNEL_DEBS[@]}"
for DEB in "$BUILD_DIR"/kernel-deps-apt/debs/*.deb; do
    [ -e "$DEB" ] || continue
    pool_add --chroot "$DEB"
done

# Packages from config/packages.binary/ for pkgsel to install on the
# target, on top of the base system and the standard task.
POOL_INCLUDE=()

FIRMWARE_ARGS=()

# Toggle, 1 to enable: add the fetched firmware (ath12k wifi, SoC
# remoteprocs/GPU) to the installer initrds. Without it the installer has no
# wifi, and the remoteprocs/GPU fail to load their firmware. 0 keeps the
# initrds small, which GRUB is slow to load.
INITRD_FIRMWARE=${INITRD_FIRMWARE:-1}

if [ "$ARCH" = "arm64" ]; then
    stage "Fetch firmware"

    # Backports: the WCN7850 firmware in trixie is too old for the custom
    # kernel's ath12k (WMI command timeouts when the interface comes up).
    # Other suites (e.g. forky) are new enough, and have no backports.
    FIRMWARE_SUITE="$SUITE"
    if [ "$SUITE" = "trixie" ]; then
        FIRMWARE_SUITE="trixie-backports"
    fi

    "$SCRIPT_DIR/lib/fetch-firmware.sh" "$BUILD_DIR" "$FIRMWARE_SUITE"
    if [ "$INITRD_FIRMWARE" = 1 ]; then
        FIRMWARE_ARGS=("$BUILD_DIR/firmware")
    else
        echo "Not adding firmware to the installer initrds (INITRD_FIRMWARE=0)"
    fi

    # Also install the firmware packages on the target: they're in
    # non-free-firmware, so the pool has none of them otherwise (see
    # configure.sh). Not with FIRMWARE_DIR, which fetches no packages.
    if compgen -G "$BUILD_DIR/firmware-apt/debs/*.deb" >/dev/null; then
        for DEB in "$BUILD_DIR"/firmware-apt/debs/*.deb; do
            pool_add --chroot "$DEB"
            POOL_INCLUDE+=("$POOL_PKG")
        done
    else
        echo "No firmware packages for the target (FIRMWARE_DIR set)"
    fi

    stage "Fetch stubble"

    # The target builds a Stubble image per kernel, as the laptops' firmware
    # provides no DTB: see postinst.d/20-stubble. Add stubble's
    # dependencies to the pool too, but only list stubble for pkgsel.
    "$SCRIPT_DIR/lib/fetch-stubble.sh" "$BUILD_DIR" "$SUITE"
    for DEB in "$BUILD_DIR"/stubble-apt/debs/*.deb; do
        pool_add "$DEB"
    done
    POOL_INCLUDE+=(stubble)

    # The installer's own boot arguments, for the target's GRUB too, both
    # its Stubble entries and 10_linux's.
    # shellcheck source=/dev/null
    . "$BUILD_DIR/config/installer-config.sh"
    mkdir -p "$BUILD_DIR/config/includes.binary/qli/stubble/grub"
    cat > "$BUILD_DIR/config/includes.binary/qli/stubble/grub/custom-boot-args.cfg" <<EOF
# Generated by the installer's build.sh from CUSTOM_BOOT_ARGS in
# installer-config.sh.
GRUB_CMDLINE_LINUX="\$GRUB_CMDLINE_LINUX $CUSTOM_BOOT_ARGS"
EOF
fi

stage "Build bootstrap and chroot"

# After the fetch stages: lb chroot installs config/packages.chroot/. For
# live, rebuild the chroot every time, so it has this build's kernel and
# firmware. lb clean --chroot leaves the bootstrap's stagefiles, so remove
# those too; lb bootstrap then restores the chroot from cache/bootstrap/.
if [ "$INSTALLER" = live ]; then
    lb clean --chroot
    rm -f .build/bootstrap*
fi

lb bootstrap
lb chroot

# --linux-packages none makes binary_linux-image skip the live system's
# kernel and initrd, so put them in binary/live/ via config/includes.binary/.
# The 0100-stubble binary hook wraps the kernel with Stubble.
LIVE_BOOT="$BUILD_DIR/config/includes.binary/live"
rm -rf "$LIVE_BOOT"
if [ "$INSTALLER" = live ]; then
    mkdir -p "$LIVE_BOOT"
    cp -L chroot/boot/vmlinuz-* chroot/boot/initrd.img-* "$LIVE_BOOT/"
fi

stage "Build installer"

lb installer

stage "Unpack kernel"

KERNEL_DIR=$(mktemp -d)
trap 'rm -rf "$KERNEL_DIR"' EXIT

for DEB in "${KERNEL_DEBS[@]}"; do
    echo "Unpacking $DEB"
    dpkg-deb -x "$DEB" "$KERNEL_DIR"
done

stage "Replace installer kernel"

# configure.sh gathers the overlays' preseeds for this installer type into
# config/installer-preseed.cfg. Add the custom kernel to it: base-installer
# would otherwise look for the stock kernel's package.
PRESEED="$BUILD_DIR/config/installer-preseed.cfg"
cat >> "$PRESEED" <<EOF
# Generated by build.sh: install the custom kernel from the pool.
d-i base-installer/kernel/image string $KERNEL_PACKAGE
EOF
# pkgsel/include is a single setting, so list all of the packages at once.
if [ "${#POOL_INCLUDE[@]}" -gt 0 ]; then
    cat >> "$PRESEED" <<EOF
# Generated by build.sh: install these packages from the pool.
d-i pkgsel/include string ${POOL_INCLUDE[*]}
EOF
fi

# --module-rules leaves the modules the installer doesn't need out of
# its initrds; --initrd-overlay adds extra files to them.
"$SCRIPT_DIR/replace-di-kernel.sh" \
    --preseed "$PRESEED" \
    --module-rules "$REPO_ROOT/overlays/$ARCH/initrd-modules.rules" \
    --initrd-overlay "$REPO_ROOT/overlays/$ARCH/initrd" \
    "$KERNEL_DIR" \
    "$BUILD_DIR/binary/install" \
    "${FIRMWARE_ARGS[@]}"

if [ "$ARCH" = "arm64" ]; then
    stage "Extract DTBs"

    # All of the kernel's DTBs, not just the ones the ISO ships: the
    # 0100-stubble binary hook bundles only those with a Stubble HWID
    # entry into the Stubble EFI binary. The rest stay in config/.
    rm -rf "$BUILD_DIR/config/dtbs"
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

    # The AArch64 Stubble stub, for the 0100-stubble binary hook:
    # the host's own stubble package has the host architecture's stub.
    dpkg-deb --fsys-tarfile "$BUILD_DIR"/stubble-apt/debs/stubble_*_arm64.deb |
        tar -xO ./usr/lib/stubble/stubble.efi \
        > "$BUILD_DIR/config/stubble-aa64.efi"
fi

rm -rf "$KERNEL_DIR"
trap - EXIT

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
# binary/ tree with the chroot, wiping the installer.) live keeps them, for
# its live/filesystem.squashfs.
if [ "$INSTALLER" != live ]; then
    mkdir -p "$BUILD_DIR/.build"
    touch \
        "$BUILD_DIR/.build/binary_chroot" \
        "$BUILD_DIR/.build/binary_rootfs" \
        "$BUILD_DIR/.build/binary_manifest"
fi

stage "Build image"

lb binary

# The image is FAT32, which can't hold files of 4 GiB or more.
SQUASHFS="$BUILD_DIR/binary/live/filesystem.squashfs"
if [ -f "$SQUASHFS" ] && [ "$(stat -c %s "$SQUASHFS")" -gt 4294967295 ]; then
    echo "$SQUASHFS is too big for FAT32 (4 GiB or more)" >&2
    exit 1
fi

echo "=== Complete ==="

ls -lh ./*.img
