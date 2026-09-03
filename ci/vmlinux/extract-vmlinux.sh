#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# Extract the unstripped kernel binary (vmlinux) from a kernel debug package,
# so that oops and panic backtraces can be symbolised.
#
# Usage: extract-vmlinux.sh [--optional] <deb> [output]
#
# <deb> is either a kernel -dbg package or the linux-image package it belongs
# to, in which case the -dbg package sitting next to it is used. vmlinux is
# written to <output>, "vmlinux" in the current directory by default.
#
# Not every kernel has a debug package; --optional reports a missing one
# and exits successfully rather than failing the build.

set -eux

optional=
if [ "${1:-}" = "--optional" ]; then
    optional=yes
    shift
fi

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
    echo "usage: ${0##*/} [--optional] <deb> [output]" >&2
    exit 1
fi

kernel_deb=$1
output=${2:-vmlinux}

# Derive the name of the debug package from the file name of the given package,
# which is <package>_<version>_<architecture>.deb; both are built from the same
# source, so they only differ in the -dbg suffix of the package name. Matching
# the full file name rather than globbing the directory for a -dbg package
# keeps unrelated copies of it (backups, other versions) out of the way.
#
# The extension is stripped first, so that a name carrying no version and
# architecture at all, as staged by download-vmlinux.yaml next to it,
# is still recognised as a debug package by its -dbg suffix.
deb=$(basename "$kernel_deb" .deb)
pkg=${deb%%_*}
pkgver_arch=${deb#*_}
case "$pkg" in
  *-dbg)
    # a debug package was passed in directly
    dbg_deb=$kernel_deb
    ;;
  *)
    if [ ! -f "$kernel_deb" ]; then
        echo "ERROR: ${kernel_deb}: no such package" >&2
        exit 1
    fi
    dbg_deb="$(dirname "$kernel_deb")/${pkg}-dbg_${pkgver_arch}.deb"
    ;;
esac

if [ ! -f "$dbg_deb" ]; then
    if [ -n "$optional" ]; then
        echo "WARNING: ${dbg_deb}: no such debug package;" \
            "can't extract vmlinux" >&2
        exit 0
    fi
    echo "ERROR: ${dbg_deb}: no such debug package" >&2
    exit 1
fi

# The kernel version the package carries the debug information for; the debug
# package is named linux-image-<version>-dbg.
dbgpkg=$(dpkg-deb -f "$dbg_deb" Package)
kver=${dbgpkg#linux-image-}
kver=${kver%-dbg}

# Unpack next to the output file rather than below /tmp: vmlinux is hundreds of
# megabytes, and moving it into place is a rename this way.
workdir=$(mktemp -d "${output}.XXXXXX")
trap 'rm -rf "$workdir"' EXIT

# Extract the unstripped kernel image only. The debug package also carries
# debug information for every module, amounting to gigabytes of data that is
# not needed here. Debian puts vmlinux in /usr/lib/debug/boot/vmlinux-<ver>,
# while deb-pkg puts it in /usr/lib/debug/lib/modules/<ver>/vmlinux. Match
# either file name and require exactly one result.
#
# tar reports an error for a pattern that matches nothing, so check the
# extracted files rather than the exit status.
#
# --no-same-owner ignores the ownership recorded in the package rather than
# restoring it when running as root, as debos does.
dpkg-deb --fsys-tarfile "$dbg_deb" |
    tar -x -C "$workdir" --no-same-owner --no-anchored \
        vmlinux "vmlinux-${kver}" || true
extracted=$(find "$workdir" -type f \
    \( -name vmlinux -o -name "vmlinux-${kver}" \))
# grep counts the non-empty lines, so no match counts as none rather than one
count=$(echo "$extracted" | grep -c .)
if [ "$count" -ne 1 ]; then
    echo "ERROR: expected exactly one vmlinux in ${dbgpkg}, found ${count}" >&2
    exit 1
fi

# Make sure the extracted image belongs to the kernel the package is named
# after, and not to a different one it happens to carry.
case "$extracted" in
  */"${kver}"/vmlinux|*/"vmlinux-${kver}")
    ;;
  *)
    echo "ERROR: ${extracted} from ${dbgpkg} doesn't match kernel ${kver}" >&2
    exit 1
    ;;
esac

mv -v "$extracted" "$output"

# debos runs as root, so leave the image to whoever owns the directory it was
# written to rather than to root; a build directory usually belongs to the user
# who started the build. Runs which own the file already can't chown it and
# don't need to.
chown --reference="$(dirname "$output")" "$output" 2>/dev/null || true
