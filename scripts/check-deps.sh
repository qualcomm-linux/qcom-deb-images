#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause
#
# Validate (or install) the host packages a build use-case needs. The package
# lists live here and nowhere else, so the Makefile, CI and README refer to
# use-case names instead of repeating package lists.
#
# Usage: check-deps.sh [--install] <use-case>...
#   use-cases: host rootfs image flash test

set -eu

install=false
if [ "${1:-}" = "--install" ]; then
    install=true
    shift
fi

if [ "$#" -eq 0 ]; then
    echo "E: no use-case given" >&2
    echo "E: usage: check-deps.sh [--install] <use-case>... (host rootfs image flash test)" >&2
    exit 2
fi

# Resolve the requested use-cases to a package list. This case statement is the
# single source of truth for the recipes' host dependencies.
packages=""
for usecase in "$@"; do
    case "$usecase" in
        host)
            set_pkgs="debos make" ;;
        rootfs)
            set_pkgs="mmdebstrap debian-archive-keyring" ;;
        image)
            # debos's image-partition execs parted, sfdisk (package fdisk),
            # mkfs.vfat, mkfs.ext4 and udevadm directly, and the recipe's
            # partition-extract step runs fdisk on the host; debos only
            # Recommends all of these.
            set_pkgs="dosfstools e2fsprogs parted fdisk udev" ;;
        flash)
            set_pkgs="dosfstools mtools xmlstarlet python3-defusedxml unzip device-tree-compiler u-boot-tools file" ;;
        test)
            set_pkgs="qemu-efi-aarch64 python3-pexpect python3-pytest" ;;
        *)
            echo "E: unknown use-case: $usecase" >&2
            exit 2 ;;
    esac
    # append, skipping packages already collected (a package may belong to more
    # than one use-case, e.g. dosfstools in image and flash)
    for pkg in $set_pkgs; do
        case " $packages " in
            *" $pkg "*) ;;
            *) packages="$packages $pkg" ;;
        esac
    done
done

# Not a dpkg-based system (e.g. running the recipes natively on a non-Debian
# host); there is nothing to check.
if ! command -v dpkg >/dev/null 2>&1; then
    echo "W: dpkg not found; skipping host dependency check" >&2
    exit 0
fi

missing=""
for pkg in $packages; do
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then
        missing="$missing $pkg"
    fi
done

if [ -z "$missing" ]; then
    exit 0
fi

if [ "$install" = true ]; then
    sudo=""
    if [ "$(id -u)" -ne 0 ]; then
        sudo="sudo"
    fi
    echo "I: installing missing host packages:$missing" >&2
    echo "I: refreshing APT package lists" >&2
    $sudo apt-get update
    $sudo apt-get install -y --no-install-recommends $missing
else
    echo "E: missing host packages:$missing" >&2
    echo "E: install them, or build via make with AUTO_INSTALL_DEPS=yes" >&2
    exit 1
fi
