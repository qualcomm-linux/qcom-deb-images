#!/bin/bash
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# live-build/firmware/lib/extract-firmware.sh <pool_dir> <out_dir>
#
# Extracts the firmware the installer needs from the firmware packages lb
# binary adds to the pool (package-lists/firmware.list.binary) to <out_dir>,
# laid out like /lib/firmware, so replace-di-kernel.sh can copy it into the
# initrds. Run by the 0050-replace-di-kernel binary hook. That is the ADSP
# and CDSP images of the MODELS below, which S03qcom-adsp needs to reset the
# ADSP; the same files as the qcom-adsp initramfs-tools hook
# (debos-recipes/overlays/qcom-adsp/).
# Not the rest of the firmware (e.g. the GPU's, the wifi firmware as the
# installer installs from its own pool, or other models' images): it would
# only make the initrds bigger, which GRUB is slow to load.
set -euo pipefail

POOL_DIR=$1
OUT_DIR=$2

PACKAGES=(firmware-qcom-soc firmware-qcom-dsp)

# The models the installer supports, as <soc>/<vendor>/<model> under qcom/.
MODELS=(
    sc8280xp/LENOVO/21BX # ThinkPad X13s
    x1e80100/LENOVO/21N1 # ThinkPad T14s Gen 6
)

die() {
    echo "extract-firmware: $*" >&2
    exit 1
}

ROOT=$(mktemp -d)
trap 'rm -rf "$ROOT"' EXIT

for PKG in "${PACKAGES[@]}"; do
    mapfile -t DEBS < <(compgen -G "$POOL_DIR/*/*/*/${PKG}_*.deb" || true)
    [ "${#DEBS[@]}" -eq 1 ] ||
        die "expected exactly one ${PKG}_*.deb in $POOL_DIR, found: ${DEBS[*]}"
    echo "Extracting ${DEBS[0]}"
    dpkg-deb -x "${DEBS[0]}" "$ROOT"
done

SRC="$ROOT/usr/lib/firmware"
[ -d "$SRC" ] || SRC="$ROOT/lib/firmware"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

copy_path() {
    mkdir -p "$OUT_DIR/$(dirname "$1")"
    cp -a "$SRC/$1" "$OUT_DIR/$1"
}

# Keep any .zst/.xz suffix: the kernel loads them compressed.
for MODEL in "${MODELS[@]}"; do
    FOUND=0
    for FIRMWARE in "$SRC/qcom/$MODEL"/*; do
        case "${FIRMWARE##*/}" in
            qcadsp*.mbn*|qccdsp*.mbn*|adsp_dtbs.elf*|cdsp_dtbs.elf*)
                copy_path "${FIRMWARE#"$SRC"/}"
                FOUND=1
                ;;
        esac
    done
    [ "$FOUND" = 1 ] ||
        die "no ADSP/CDSP images for $MODEL in the firmware packages"
done

du -sh "$OUT_DIR"
