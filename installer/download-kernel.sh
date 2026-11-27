#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# Download the kernel package for the installer image from the Qualcomm
# Linux APT repository, which setup-build-host.sh adds, e.g.:
#
#   installer/download-kernel.sh qcom-next kernel/
#
# linux-image-$KERNEL is a metapackage; download the real
# linux-image-<version> package it depends on, which has the kernel, its
# modules and DTBs.
set -eu

USAGE="Usage: $0 qcom-next|qcom-7.3 OUTDIR"

if [ "$#" -ne 2 ]; then
    echo "$USAGE" >&2
    exit 1
fi
KERNEL="$1"
OUTDIR="$2"

case "$KERNEL" in
    qcom-*) ;;
    *)
        echo "Unknown kernel '$KERNEL'" >&2
        echo "$USAGE" >&2
        exit 1
        ;;
esac

META="linux-image-$KERNEL"
PKG=$(apt-cache depends --no-recommends --no-suggests "$META" |
    awk '$1 ~ /Depends:$/ && $2 ~ /^linux-image-/ { print $2; exit }')
if [ -z "$PKG" ]; then
    echo "$META depends on no linux-image package" >&2
    exit 1
fi

mkdir -p "$OUTDIR"
(cd "$OUTDIR" && apt-get download "$PKG")
ls -l "$OUTDIR"
