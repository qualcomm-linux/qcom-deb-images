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
USAGE="Usage: $0 [--arch amd64|arm64] [--installer cdrom] [--suite SUITE] [--kernel-name NAME] [--build-id ID] KERNEL.deb..."

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

# --module-rules leaves the modules the installer doesn't need out of
# its initrds; --initrd-overlay adds extra files to them.
"$SCRIPT_DIR/replace-di-kernel.sh" \
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
mkdir -p "$BUILD_DIR/.build"
touch \
    "$BUILD_DIR/.build/binary_chroot" \
    "$BUILD_DIR/.build/binary_rootfs" \
    "$BUILD_DIR/.build/binary_manifest"

stage "Build image"

lb binary

echo "=== Complete ==="

ls -lh ./*.img
