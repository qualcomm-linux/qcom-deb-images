#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# generates a FAT image for the dtb_a/dtb_b partitions from a tree of device
# trees, as found in dtbs.tar.gz or /usr/lib/linux-image-<version>; the image
# is either a combineddtb holding a single board's device tree, or a multidtb
# holding a per-SoC FIT image built with qcom-dtb-metadata
#
# usage:
#   gen-dtb-image.sh --type combineddtb --dtb-src DIR --dtb qcom/BOARD.dtb \
#       --out FILE
#   gen-dtb-image.sh --type multidtb --dtb-src DIR --soc SOC --out FILE \
#       [--qcom-dtb-metadata DIR]

set -eux

# dtb image type, combineddtb or multidtb
TYPE=""
# root of the device tree tree, holding e.g. qcom/BOARD.dtb
DTB_SRC=""
# device tree to use relative to DTB_SRC; only for combineddtb
DTB=""
# SoC to build a FIT image for; only for multidtb
SOC=""
# output image
OUT=""
# path to qcom-dtb-metadata tree; only for multidtb
QCOM_DTB_METADATA="/usr/share/qcom-dtb-metadata"

usage() {
    echo "Usage: $0 --type combineddtb|multidtb --dtb-src DIR" \
        "[--dtb DTB | --soc SOC] --out FILE [--qcom-dtb-metadata DIR]" >&2
    exit 1
}

while [ $# -gt 0 ]; do
    [ $# -ge 2 ] || usage
    case "$1" in
        --type) TYPE="$2" ;;
        --dtb-src) DTB_SRC="$2" ;;
        --dtb) DTB="$2" ;;
        --soc) SOC="$2" ;;
        --out) OUT="$2" ;;
        --qcom-dtb-metadata) QCOM_DTB_METADATA="$2" ;;
        *) usage ;;
    esac
    shift 2
done

if [ -z "${DTB_SRC}" ] || [ -z "${OUT}" ]; then
    usage
fi

rm -f "${OUT}"

case "${TYPE}" in
    combineddtb)
        [ -n "${DTB}" ] || usage
        # dtb-combineddtb.bin is only used in UFS based boards at the moment
        # and UFS uses a 4k sector size, so pass -S 4096
        # in qcom-ptool/platforms/*/partitions.conf, dtb_a and _b partitions
        # are provisioned with 64MiB; create a 4MiB FAT that will comfortably
        # fit in these and hold the target device tree, which is 4096 KiB
        # sized blocks for mkfs.vfat's last argument
        mkfs.vfat -S 4096 -C "${OUT}" 4096
        # copy into the FAT as combined-dtb.dtb
        mcopy -vmp -i "${OUT}" "${DTB_SRC}/${DTB}" ::/combined-dtb.dtb
        ;;
    multidtb)
        [ -n "${SOC}" ] || usage
        # --soc filters the FIT down to just that SoC's device trees, as a
        # single FIT across all SoCs overflows the FAT; --prune drops configs
        # whose DTBs/DTBOs aren't in DTB_SRC, and fails if none are left
        if ! "${QCOM_DTB_METADATA}/build-dtb-image.sh" \
            --dtb-src "${DTB_SRC}/qcom" \
            --soc "${SOC}" \
            --size 2 \
            --out "${OUT}" \
            --prune; then
            # drop any partial output
            rm -f "${OUT}"
            exit 1
        fi
        ;;
    *)
        usage
        ;;
esac
