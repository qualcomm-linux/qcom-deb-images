#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

set -eux

output_dir=$1

# use strings and word splitting as lists to construct output tarballs.
# caveat is that flash_* directories cannot have spaces in them.
emmc_dirs=""
ufs_dirs=""

for d in flash_*
do
	partitions_conf="$d/partitions.conf"
	echo "examining $partitions_conf"
	if grep disk-sdcard "$partitions_conf"
	then
		echo "partitions.conf refers to disk-sdcard, choosing emmc"
		target=emmc
	elif grep disk-ufs "$partitions_conf"
	then
		echo "partitions.conf refers to disk-ufs, choosing emmc"
		target=ufs
	else
		echo "partitions.conf has unknown format, putting in emmc by default"
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

# word splitting is a feature in this case
# shellcheck disable=SC2086
tar -cvzf "$output_dir/flash-emmc.tar.gz" $emmc_dirs

# word splitting is a feature in this case
# shellcheck disable=SC2086
tar -cvzf "$output_dir/flash-ufs.tar.gz" $ufs_dirs
