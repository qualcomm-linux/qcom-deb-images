#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

set -eux

output_dir=$1
prefix=$2
boards_json_file=${3:-}

# use strings and word splitting as lists to construct output tarballs.
# caveat is that flash_* directories cannot have spaces in them.
emmc_dirs=""
ufs_dirs=""
boards_json_entries=""

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

    # extract board name and storage type from the flash dir name
    # flash_${board_name}_${disk_type}: board names use hyphens only
    dir_name="${d#flash_}"
    disk_type="${dir_name##*_}"
    board_name="${dir_name%_*}"
    entry="{\"name\":\"${board_name}\",\"storage\":\"${disk_type}\",\"flash_tarball\":\"${prefix}-flash-${target}.tar.gz\"}"
    boards_json_entries="${boards_json_entries:+${boards_json_entries},}${entry}"

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

# word splitting is a feature in this case
# shellcheck disable=SC2086
tar -cvzf "$output_dir/$prefix-flash-emmc.tar.gz" disk-sdcard.img1 disk-sdcard.img2 $emmc_dirs

# word splitting is a feature in this case
# shellcheck disable=SC2086
tar -cvzf "$output_dir/$prefix-flash-ufs.tar.gz" disk-ufs.img1 disk-ufs.img2 $ufs_dirs

if [ -n "$boards_json_file" ]; then
    printf '[%s]\n' "$boards_json_entries" >"$boards_json_file"
fi
