#!/usr/bin/env bash
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

set -euo pipefail

# Generate the live-build configuration for one installer type and suite in
# build/$ARCH/$SUITE/$INSTALLER/, then copy the overlays on top of it. Called
# by build.sh; run from anywhere.
REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

ARCH="$(dpkg --print-architecture)"
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
# cdrom installer installs from the pool. So only cache the bootstrap.
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
# With APT_PROXY set (e.g. http://host.containers.internal:3142 for
# apt-cacher-ng on the podman host), fetch the bootstrap, chroot, pool debs
# and udebs through it. The installed system's sources come from the binary
# mirrors, which stay at the defaults. lb saves the parent mirrors separately
# and doesn't rederive them, so set both, and always: an unset APT_PROXY must
# undo a previous proxied run. Keep the trailing slashes: lb appends "dists"
# to the debian-installer mirror directly.
MIRROR="http://deb.debian.org/debian/"
MIRROR_SECURITY="http://security.debian.org/"
if [ -n "${APT_PROXY:-}" ]; then
    MIRROR="${APT_PROXY%/}/deb.debian.org/debian/"
    MIRROR_SECURITY="${APT_PROXY%/}/security.debian.org/"
    echo "Using APT proxy: $APT_PROXY"
fi
MIRROR_ARGS=()
for mirror in bootstrap chroot debian-installer; do
    MIRROR_ARGS+=(--mirror-"$mirror" "$MIRROR" --parent-mirror-"$mirror" "$MIRROR")
done
MIRROR_ARGS+=(--mirror-chroot-security "$MIRROR_SECURITY"
    --parent-mirror-chroot-security "$MIRROR_SECURITY")

# Temporary: build a FAT32 disk image rather than an ISO, so the kernel
# needs no ISO 9660 support. cdrom-detect falls back to mounting disks and
# USB partitions as vfat, and the UEFI firmware boots the FAT partition.
lb config \
    "${MIRROR_ARGS[@]}" \
    --distribution "$SUITE" \
    --architectures "$ARCH" \
    --archive-areas "main non-free-firmware" \
    --parent-archive-areas "main non-free-firmware" \
    --linux-packages none \
    --debian-installer "$INSTALLER" \
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
# arch-specific overlay, so the latter wins. lb config never touches or
# removes existing files under config/hooks/ or config/includes.binary/, so
# this is safe to rerun alongside `lb config` on every invocation.
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
    # initrd without fakeroot, which can't mknod in a container.)
    if compgen -G "$overlay_dir/preseed/$INSTALLER/*.cfg" >/dev/null; then
        cat "$overlay_dir/preseed/$INSTALLER/"*.cfg >> "$PRESEED"
    fi

    if [ -d "$overlay_dir/lib" ]; then
        cp -a "$overlay_dir/lib/." "$BUILD_DIR/config/"
    fi
}

# The preseed is appended to per overlay, so start afresh.
PRESEED="$BUILD_DIR/config/installer-preseed.cfg"
rm -f "$PRESEED"

for overlay_dir in "${OVERLAY_DIRS[@]}"; do
    apply_overlay "$overlay_dir"
done

# Put tasksel's "standard system utilities" task (every package of
# Priority: standard, see installer-pool.list.binary) in the pool, so pkgsel
# installs all of it offline. Generated here rather than as an overlay list
# because lb runs "!" lines even inside an #if that doesn't match.
STANDARD_LIST="$BUILD_DIR/config/package-lists/standard-task.list.binary"
mkdir -p "$(dirname "$STANDARD_LIST")"
cat > "$STANDARD_LIST" <<'EOF'
# Generated by scripts/configure.sh: tasksel's standard task.
! grep-aptavail -n -s Package -F Priority -X standard
EOF

# Add the installer type, suite, kernel name and build ID to the boot menu's
# title, e.g. "Debian Installer (Qualcomm) - forky cdrom - qcom-laptops - QLI
# Installer - build 123456789-1"; the kernel name and build ID are left out
# when not given. apply_overlay copies theme.txt afresh on every run, so this
# never stacks.
TITLE=" - $SUITE $INSTALLER"
if [ -n "$KERNEL_NAME" ]; then
    TITLE+=" - $KERNEL_NAME"
fi
TITLE+=" - QLI Installer"
if [ -n "$BUILD_ID" ]; then
    TITLE+=" - build $BUILD_ID"
fi
THEME="$BUILD_DIR/config/bootloaders/grub-pc/live-theme/theme.txt"
if [ -f "$THEME" ]; then
    sed -i -E "s|^(title-text: \"[^\"]*)\"|\1$TITLE\"|" "$THEME"
fi

echo "Configured live-build in: $BUILD_DIR"
