#!/usr/bin/env bash
# Build a debian-installer ISO with live-build in a fresh installer/build/
# directory; the ISO is left in installer/build/.
set -euo pipefail

ARCH="arm64"
SUITE="trixie"
INSTALLER="cdrom"
USAGE="Usage: $0 [--arch ARCH] [--suite SUITE] [--installer netinst|cdrom]"

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
        --installer)
            INSTALLER="${2:?Missing value for --installer}"
            shift 2
            ;;
        *)
            echo "$USAGE" >&2
            exit 1
            ;;
    esac
done

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
BUILD_DIR="$REPO_ROOT/build"

# always start from scratch; fails if a previous build was left behind
mkdir "$BUILD_DIR"
cd "$BUILD_DIR"

echo "=== Configure live-build ==="

lb config \
    --distribution "$SUITE" \
    --architectures "$ARCH" \
    --linux-packages none \
    --debian-installer "$INSTALLER" \
    --debian-installer-distribution "$SUITE" \
    --debian-installer-gui true

# overlays/$ARCH/ mirrors live-build's config/ and is copied on top of it
if [ -d "$REPO_ROOT/overlays/$ARCH" ]; then
    cp -av "$REPO_ROOT/overlays/$ARCH/." config/
fi

echo "=== Build bootstrap, chroot and installer ==="

lb bootstrap
lb chroot
lb installer

# live-build only ships udeb_include lists for amd64, and binary_disk fails
# for cdrom when ${ARCH}_udeb_include is missing. It leaves an existing
# binary/.disk/udeb_include alone, so pre-seed ours from the overlay.
UDEB_INCLUDE="config/debian-cd/${INSTALLER}_udeb_include"
if [ -f "$UDEB_INCLUDE" ]; then
    install -D -m 0644 "$UDEB_INCLUDE" binary/.disk/udeb_include
fi

echo "=== Build ISO ==="

lb binary

ls -lh ./*.iso
