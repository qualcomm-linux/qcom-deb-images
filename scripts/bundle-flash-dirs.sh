#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

set -eux

output_dir=$1
prefix=$2

# use strings and word splitting as lists to construct output tarballs.
# caveat is that flash_* directories cannot have spaces in them.
emmc_dirs=""
ufs_dirs=""

for d in flash_*
do
    rawprogram0="$d/rawprogram0.xml"
    echo "examining $rawprogram0"
    rootfs_img=$("$(dirname "$0")"/get-rawprogram-filename.py rootfs "$rawprogram0")
    if echo "$rootfs_img" | grep -q disk-sdcard
    then
        echo "choosing emmc"
        target=emmc
    elif echo "$rootfs_img" | grep -q disk-ufs
    then
        echo "choosing ufs"
        target=ufs
    else
        echo "couldn't find disk-ufs or disk-emmc, choosing emmc by default"
        target=emmc
    fi

    echo "choosen target $target for $d"

    case "$target" in
        emmc)
            emmc_dirs="$emmc_dirs $d"
            ;;
        ufs)
            ufs_dirs="$ufs_dirs $d"
            ;;
    esac
done

echo "emmc_dirs: $emmc_dirs"
echo "ufs_dirs: $ufs_dirs"

# per-SoC multi-DTB FIT images shared across boards; glob into a list so all
# of them land in each bundle (there may be none for a given build)
multidtb_bins=""
for f in dtb-multidtb-*.bin
do
    [ -e "$f" ] && multidtb_bins="$multidtb_bins $f"
done
echo "multidtb_bins: $multidtb_bins"

# the partition images are symlinks to the arm64 artifacts, and the rawprogram
# XMLs in the flash directories refer to them under these names, so archive the
# files they point at rather than the symlinks
# word splitting is a feature in this case
# shellcheck disable=SC2086
tar -chvzf "$output_dir/$prefix-flash-emmc.tar.gz" disk-sdcard.img1 disk-sdcard.img2 $multidtb_bins $emmc_dirs

# word splitting is a feature in this case
# shellcheck disable=SC2086
tar -chvzf "$output_dir/$prefix-flash-ufs.tar.gz" disk-ufs.img1 disk-ufs.img2 $multidtb_bins $ufs_dirs
