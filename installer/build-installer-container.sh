#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# Build the installer image in a privileged forky container, as
# .github/workflows/installer.yml does, so the host needs nothing but
# podman or docker. The kernel comes from the Qualcomm Linux APT
# repository, e.g.:
#
#   installer/build-installer-container.sh --suite forky --kernel qcom-next
#
# A locally built kernel package can be given instead; --kernel then only
# names the build directory:
#
#   installer/build-installer-container.sh --suite forky --kernel my-kernel ../linux-image-custom.deb
#
# The image is written to installer/build/$SUITE-$KERNEL/, as without a
# container.
set -eu

ENGINE=""
IMAGE="public.ecr.aws/debian/debian:forky"
SUITE="trixie"
KERNEL=""
DEB=""
USAGE="Usage: $0 --kernel qcom-next|qcom-7.3 [--suite SUITE] [--engine podman|docker]
       $0 [--kernel NAME] [--suite SUITE] [--engine podman|docker] KERNEL.deb"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --kernel)
            KERNEL="${2:?Missing value for --kernel}"
            shift 2
            ;;
        --suite)
            SUITE="${2:?Missing value for --suite}"
            shift 2
            ;;
        --engine)
            ENGINE="${2:?Missing value for --engine}"
            shift 2
            ;;
        -h|--help)
            echo "$USAGE"
            exit 0
            ;;
        -*)
            echo "$USAGE" >&2
            exit 1
            ;;
        *)
            if [ -n "$DEB" ]; then
                echo "$USAGE" >&2
                exit 1
            fi
            if [ ! -f "$1" ]; then
                echo "$1: no such file" >&2
                exit 1
            fi
            DEB=$(realpath "$1")
            shift
            ;;
    esac
done

# Without a local package, the kernel is linux-image-$KERNEL from the
# Qualcomm Linux repository.
if [ -z "$DEB" ]; then
    case "$KERNEL" in
        qcom-*) ;;
        *)
            echo "$USAGE" >&2
            exit 1
            ;;
    esac
fi

if [ -z "$ENGINE" ]; then
    if command -v podman >/dev/null 2>&1; then
        ENGINE=podman
    elif command -v docker >/dev/null 2>&1; then
        ENGINE=docker
    else
        echo "Neither podman nor docker found" >&2
        exit 1
    fi
fi

SRC_DIR=$(cd "$(dirname "$0")/.." && pwd)

# live-build and d-i's initrd handling need loop devices and mknod, which
# rootless podman cannot do even with --privileged, so run podman as root.
SUDO=""
if [ "$ENGINE" = podman ] && [ "$(id -u)" -ne 0 ] &&
    [ "$(podman info --format '{{.Host.Security.Rootless}}')" = true ]; then
    SUDO=sudo
fi

# Create the build directory as the host user: the build runs as root, which
# would otherwise create it, and leave later scripts (e.g. the QEMU one)
# unable to write next to the image.
mkdir -p "$SRC_DIR/installer/build"

# A local kernel package is mounted read-only, as it may live outside the
# source tree.
set --
LOCAL_KERNEL=""
if [ -n "$DEB" ]; then
    set -- -v "$DEB:/kernel-local/$(basename "$DEB"):ro"
    LOCAL_KERNEL=1
fi

# The script below runs in the container, reading its parameters from the
# environment. The host's /dev is bind-mounted so that loop devices
# losetup creates during the build show up in the container.
$SUDO "$ENGINE" run --rm -i --privileged \
    -v /dev:/dev "$@" \
    -v "$SRC_DIR:/src" -w /src \
    -e SUITE="$SUITE" \
    -e KERNEL="$KERNEL" \
    -e LOCAL_KERNEL="$LOCAL_KERNEL" \
    -e HOST_UID="$(id -u)" \
    -e HOST_GID="$(id -g)" \
    "$IMAGE" sh -eux -s <<'EOF'
installer/setup-build-host.sh --suite "$SUITE"

if [ -n "$LOCAL_KERNEL" ]; then
    kernel_debs=/kernel-local
else
    kernel_debs=/tmp/kernel
    installer/download-kernel.sh "$KERNEL" "$kernel_debs"
fi

set -- --suite "$SUITE"
if [ -n "$KERNEL" ]; then
    set -- "$@" --kernel-name "$KERNEL"
fi
installer/build-installer.sh "$@" "$kernel_debs"/*.deb
# The build runs as root, so give the build tree back to the host user.
chown -R "$HOST_UID:$HOST_GID" installer/build
EOF
