#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# Prepare a Debian forky host or container, as root, to build the
# installer image: install live-build from git with its dependencies,
# stubble, and the Qualcomm Linux APT repository for the suite the image
# installs, which download-kernel.sh downloads the kernel from, e.g.:
#
#   installer/setup-build-host.sh --suite trixie
#
# Build on forky whatever the target suite, for its newer live-build
# dependencies and stubble; live-build bootstraps the target suite itself.
set -eu

SUITE="trixie"
# live-build from git rather than the suite's package, pinned to a known
# commit so that the build only changes when the pin is bumped.
LIVE_BUILD_REPO="https://salsa.debian.org/live-team/live-build.git"
LIVE_BUILD_REF="531cdb9854e04d08c3c3770463454d8a5e91b129"
USAGE="Usage: $0 [--suite SUITE]"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --suite)
            SUITE="${2:?Missing value for --suite}"
            shift 2
            ;;
        -h|--help)
            echo "$USAGE"
            exit 0
            ;;
        *)
            echo "$USAGE" >&2
            exit 1
            ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this script as root" >&2
    exit 1
fi

# Get the latest fixes first, and avoid an automated update while
# building.
apt-get update
apt-get -y full-upgrade

# live-build's own dependencies (debootstrap, cpio) and recommends, as it's
# installed from git below, plus what installing it from git needs: po4a and
# gettext for its manpages, and dpkg-dev.
# dctrl-tools: grep-aptavail lists tasksel's standard task for the pool.
# stubble: stubblify, the Stubble EFI stub and its HWID database.
apt-get -y install \
    git \
    make \
    gettext \
    dpkg-dev \
    po4a \
    debootstrap \
    cpio \
    bzip2 \
    file \
    rsync \
    wget \
    apt-utils \
    fakeroot \
    xorriso \
    device-tree-compiler \
    ca-certificates \
    sudo \
    kmod \
    squashfs-tools \
    zstd \
    xz-utils \
    debian-archive-keyring \
    dctrl-tools \
    stubble

LIVE_BUILD_SRC=$(mktemp -d)
git init "$LIVE_BUILD_SRC"
git -C "$LIVE_BUILD_SRC" fetch --depth 1 "$LIVE_BUILD_REPO" "$LIVE_BUILD_REF"
git -C "$LIVE_BUILD_SRC" checkout --detach FETCH_HEAD
make -C "$LIVE_BUILD_SRC" install
rm -rf "$LIVE_BUILD_SRC"
lb --version

cat > /etc/apt/sources.list.d/qli.sources <<EOF
# Qualcomm Linux repository
Types: deb
URIs: https://deb.debusine.qualcomm.com/qualcomm/qli
Suites: $SUITE
Components: main contrib non-free-firmware non-free
Enabled: yes
Signed-By:
 -----BEGIN PGP PUBLIC KEY BLOCK-----
 .
 mDMEag8p/xYJKwYBBAHaRw8BAQdAdB6JSNF1OXxnsTgp4VTUekW52BM7e6ZQVRsq
 QT5QDaS0JEFyY2hpdmUgc2lnbmluZyBrZXkgZm9yIHF1YWxjb21tL3FsaYiQBBMW
 CgA4FiEEOwuFfyf8aPE5SQakb8qSvoHfw8IFAmoPKf8CGwMFCwkIBwIGFQoJCAsC
 BBYCAwECHgECF4AACgkQb8qSvoHfw8Lz1gEA9XocADbvqUgZQc0LceThn7vMI98d
 kTJoiInuulQ6rEUBANo+GOKILH71VRnZ5jWtsu7IlVk7oUMlTtC0eE5tcBwB
 =bX6V
 -----END PGP PUBLIC KEY BLOCK-----
EOF
apt-get update
