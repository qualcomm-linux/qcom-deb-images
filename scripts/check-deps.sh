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
            # ipxe-qemu ships the virtio-net option ROM (efi-virtio.rom) that
            # fakemachine's qemu/kvm backends need for their default NIC; it
            # is only a Recommends of qemu-system-arm, so --no-install-recommends
            # drops it.
            set_pkgs="debos make ipxe-qemu"
            # fakemachine boots a kernel from the host's /boot; containers
            # (like the CI job container) ship none, and debos only Recommends
            # the kernel meta-package. Add it where the current distribution
            # and architecture provide one under this name (e.g. Debian
            # amd64/arm64); elsewhere the host's own kernel serves. Needs APT
            # lists to resolve (CI runs apt update first).
            kernel_pkg="linux-image-$(dpkg --print-architecture 2>/dev/null || true)"
            if apt-cache show "$kernel_pkg" >/dev/null 2>&1; then
                set_pkgs="$set_pkgs $kernel_pkg"
            fi
            ;;
        rootfs)
            set_pkgs="mmdebstrap debian-archive-keyring" ;;
        image)
            # debos's image-partition execs parted, sfdisk (package fdisk),
            # mkfs.vfat, mkfs.ext4 and udevadm directly, and the recipe's
            # partition-extract step runs fdisk on the host; debos only
            # Recommends all of these.
            set_pkgs="dosfstools e2fsprogs parted fdisk udev" ;;
        flash)
            # ca-certificates' postinst generates the CA bundle
            # /etc/ssl/certs/ca-certificates.crt that debos, a Go binary,
            # verifies its https download actions against; it is pulled in
            # via Recommends, so --no-install-recommends drops it.
            set_pkgs="dosfstools mtools xmlstarlet python3-defusedxml unzip device-tree-compiler u-boot-tools file ca-certificates" ;;
        test)
            # qemu-utils ships qemu-img, which ci/qemu_test.py runs first to
            # build the boot overlay, and ipxe-qemu the default NIC's option
            # ROM; both are only Recommends of qemu-system-arm (itself a debos
            # Depends, hence not listed).
            set_pkgs="qemu-efi-aarch64 qemu-utils ipxe-qemu python3-pexpect python3-pytest" ;;
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

if [ "$install" = true ]; then
    # apt-get install is idempotent: it acts only on packages that are missing
    # or need configuring, so let APT resolve the set rather than pre-checking
    # it ourselves.
    sudo=""
    if [ "$(id -u)" -ne 0 ]; then
        sudo="sudo"
    fi
    echo "I: ensuring host packages:$packages" >&2
    echo "I: refreshing APT package lists" >&2
    $sudo apt-get update
    $sudo apt-get install -y --no-install-recommends $packages
    exit 0
fi

# Validate only. A package counts as present only when dpkg records it as fully
# "installed". "dpkg -s" succeeds (exit 0) even for config-files (removed but not
# purged) and half-configured states, where the tool is not actually usable, so
# check the status field and require the "ok installed" form (any want, so a
# held-back-but-installed package still counts).
missing=""
for pkg in $packages; do
    status="$(dpkg-query -f '${Status}' -W "$pkg" 2>/dev/null || true)"
    case "$status" in
        *" ok installed") ;;
        *) missing="$missing $pkg" ;;
    esac
done

if [ -n "$missing" ]; then
    echo "E: missing host packages:$missing" >&2
    echo "E: install them, or build via make with AUTO_INSTALL_DEPS=yes" >&2
    exit 1
fi
