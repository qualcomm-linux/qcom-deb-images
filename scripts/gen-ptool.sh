#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# generates a modified partitions.conf, a disk-type stamp,
# ptool-partitions.xml, ptool files and contents.xml for target platform and
# parameters

set -eux

# path to ptool tree
QCOM_PTOOL="$1"
# ptool subdir for a particular platform and storage, e.g.
# "qrb2210/emmc-16GB-arduino"
PLATFORM="$2"
# relative path to CDT, to use as filename in the generated files
CDT_FILENAME="$3"
# build id for generated contents.xml
BUILDID="$4"
# disk storage, emmc, nvme, spinor or ufs
DISK_TYPE="$5"
# dtb type: multidtb (shared ../dtb-multidtb.bin at ARTIFACTDIR level) or
# combineddtb (local dtb-combineddtb.bin in flash dir); default is combineddtb
DTB_TYPE="${6:-combineddtb}"

PARTITIONS_CONF="${QCOM_PTOOL}/platforms/${PLATFORM}/partitions.conf"

case "$DISK_TYPE" in
  emmc|nvme)
    esp="../disk-sdcard.img1"
    rootfs="../disk-sdcard.img2"
    ;;
  ufs)
    esp="../disk-ufs.img1"
    rootfs="../disk-ufs.img2"
    ;;
  spinor)
    # spinor carries firmware only; no OS efi/rootfs partitions
    esp=""
    rootfs=""
    ;;
  *)
    echo "unsupported disk type $DISK_TYPE"
    exit 1
    ;;
esac

case "$DTB_TYPE" in
  multidtb)
    dtb="../dtb-multidtb.bin"
    ;;
  combineddtb)
    dtb="dtb-combineddtb.bin"
    ;;
  *)
    echo "unsupported dtb type $DTB_TYPE"
    exit 1
    ;;
esac

# build a map of partition names from partitions.conf to our names
#
# |--------|--------------|-------------------|-----------------|
# | data   | ptool name   | ptool filename    | debos filename  |
# |--------|--------------|-------------------|-----------------|
# | ESP    | efi          | efi.bin           | disk-media.img1 |
# | rootfs | rootfs       | rootfs.img        | disk-media.img2 |
# | DTBs   | dtb_a, dtb_b | dtb.bin           | dtb.bin         |
# | CDTs   | cdt          | unset / per board | from download   |
# |--------|--------------|-------------------|-----------------|
partition_map="cdt=$(basename "${CDT_FILENAME}")"
partition_map="${partition_map},dtb_a=${dtb}"
partition_map="${partition_map},dtb_b=${dtb}"
partition_map="${partition_map},efi=${esp}"
partition_map="${partition_map},rootfs=${rootfs}"

# generate ptool-partitions.xml from partitions.conf
"${QCOM_PTOOL}/gen_partition.py" -i "${PARTITIONS_CONF}" \
    -o ptool-partitions.xml \
    -m "${partition_map}"

# generate contents.xml from ptool-partitions.xml and contents.xml.in
CONTENTS="${QCOM_PTOOL}/platforms/${PLATFORM}/contents.xml.in"
if [ -e "$CONTENTS" ]; then
    "${QCOM_PTOOL}/gen_contents.py" -p ptool-partitions.xml \
        -t "$CONTENTS" \
        -b "$BUILDID" \
        -o contents.xml
fi

# generate flashing files from qcom-partitions.xml
"${QCOM_PTOOL}/ptool.py" -x ptool-partitions.xml
