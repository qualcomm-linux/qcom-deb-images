#!/usr/bin/env bash
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

set -euo pipefail

ARCH="$(dpkg --print-architecture)"
INSTALLER="cdrom"
SUITE="trixie"
KERNEL_DEBS=()
USAGE="Usage: $0 [--arch amd64|arm64] [--installer cdrom] [--suite SUITE] KERNEL.deb..."

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

# Only the cdrom installer for now: it installs the base system from the
# pool on the ISO.
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

# Print a stage's name, and record how long the previous one took for the
# summary at the end.
STAGE_NAME=""
STAGE_START=$SECONDS
STAGE_TIMES=()
stage() {
    if [ -n "$STAGE_NAME" ]; then
        STAGE_TIMES+=("$(printf '%5ds  %s' $((SECONDS - STAGE_START)) "$STAGE_NAME")")
    fi
    STAGE_NAME="$1"
    STAGE_START=$SECONDS
    if [ -n "$1" ]; then
        echo "=== $1 ==="
    fi
}

stage "Configure live-build"

"$SCRIPT_DIR/configure.sh" --arch "$ARCH" --suite "$SUITE" --installer "$INSTALLER"

stage "Clean previous binary build"

lb clean --binary

stage "Build bootstrap and chroot"

lb bootstrap
lb chroot

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
# 0060-drop-stock-kernel hook drops the stock kernel, keeping the debs listed
# in config/kernel-debs.list.
PACKAGES_BINARY="$BUILD_DIR/config/packages.binary"
KERNEL_DEBS_LIST="$BUILD_DIR/config/kernel-debs.list"
rm -rf "$PACKAGES_BINARY"
mkdir -p "$PACKAGES_BINARY"
: > "$KERNEL_DEBS_LIST"
KERNEL_PACKAGE=""
for DEB in "${KERNEL_DEBS[@]}"; do
    PKG=$(dpkg-deb -f "$DEB" Package)
    VER=$(dpkg-deb -f "$DEB" Version)
    NAME="${PKG}_${VER#*:}_$(dpkg-deb -f "$DEB" Architecture).deb"
    cp "$DEB" "$PACKAGES_BINARY/$NAME"
    echo "$NAME" >> "$KERNEL_DEBS_LIST"
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

lb installer

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

    # Temporary, along with qcom-laptop-postinst.sh: ship the SoC firmware on
    # the ISO as /qli/firmware/, which qcom-laptop-postinst.sh copies just the
    # ADSP firmware from.
    QLI_FIRMWARE="$BUILD_DIR/config/includes.binary/qli/firmware"
    rm -rf "$QLI_FIRMWARE"
    if [ -d "$BUILD_DIR/firmware/qcom/x1e80100" ]; then
        mkdir -p "$QLI_FIRMWARE/qcom"
        cp -a "$BUILD_DIR/firmware/qcom/x1e80100" "$QLI_FIRMWARE/qcom/"
    fi
fi

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
PRESEED_ARGS=(--preseed "$PRESEED")

# Leave the modules the installer doesn't need out of its initrds.
MODULE_RULES_ARGS=()
if [ -f "$REPO_ROOT/overlays/$ARCH/initrd-modules.rules" ]; then
    MODULE_RULES_ARGS=(--module-rules "$REPO_ROOT/overlays/$ARCH/initrd-modules.rules")
fi

# Extra files for the installer's initrds.
INITRD_OVERLAY_ARGS=()
if [ -d "$REPO_ROOT/overlays/$ARCH/initrd" ]; then
    INITRD_OVERLAY_ARGS=(--initrd-overlay "$REPO_ROOT/overlays/$ARCH/initrd")
fi

"$SCRIPT_DIR/replace-di-kernel.sh" \
    "${PRESEED_ARGS[@]}" \
    "${MODULE_RULES_ARGS[@]}" \
    "${INITRD_OVERLAY_ARGS[@]}" \
    "$KERNEL_DIR" \
    "$BUILD_DIR/binary/install" \
    "${FIRMWARE_ARGS[@]}"

if [ "$ARCH" = "arm64" ]; then
    stage "Extract DTBs"

    # All of the kernel's DTBs, not just the ones the ISO ships: the
    # 0100-qcom-laptop-boot binary hook bundles only those with a Stubble HWID
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
fi

rm -rf "$KERNEL_DIR"
trap - EXIT

# live-build only ships udeb_include lists for amd64, and binary_disk fails
# for cdrom when ${ARCH}_udeb_include is missing. It leaves an existing
# binary/.disk/udeb_include alone, so pre-seed ours from the overlay.
UDEB_INCLUDE="$BUILD_DIR/config/debian-cd/${INSTALLER}_udeb_include"
if [ -f "$UDEB_INCLUDE" ]; then
    install -D -m 0644 "$UDEB_INCLUDE" "$BUILD_DIR/binary/.disk/udeb_include"
fi

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

stage ""

echo "=== Complete ==="

printf '%s\n' "${STAGE_TIMES[@]}"
printf '%5ds  total\n' "$SECONDS"
ls -lh ./*.img
