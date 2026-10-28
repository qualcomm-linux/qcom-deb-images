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
# dtb type: multidtb (shared ../dtb-multidtb-<soc>.bin at ARTIFACTDIR level)
# or combineddtb (local dtb-combineddtb.bin in flash dir); default is
# combineddtb
DTB_TYPE="${6:-combineddtb}"
# SoC id, used to pick the per-SoC ../dtb-multidtb-<soc>.bin; only required for
# the multidtb dtb type
SOC="${7:-}"

# board name is the first path component of PLATFORM (e.g. "maili-mtp" from
# "maili-mtp/ufs"); migrated boards live as platforms/boards/<board>.yaml and
# the per-storage layout is selected by DISK_TYPE, which matches the YAML
# storage "id" (ufs/emmc/nvme/spinor). Legacy boards keep partitions.conf.
BOARD="${PLATFORM%%/*}"
BOARD_YAML="${QCOM_PTOOL}/platforms/boards/${BOARD}.yaml"
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
    if [ -z "$SOC" ]; then
      echo "multidtb dtb type requires a SoC id (arg 7)"
      exit 1
    fi
    dtb="../dtb-multidtb-${SOC}.bin"
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

# qcom-ptool ships as the "qcom_ptool" Python package; run its scripts as
# modules with the tree root on PYTHONPATH so intra-package imports resolve
# without a pip install
export PYTHONPATH="${QCOM_PTOOL}${PYTHONPATH:+:${PYTHONPATH}}"

# generate ptool-partitions.xml, from the migrated YAML board file if one
# exists, else from the legacy per-platform partitions.conf. In --board mode
# the storage is picked by id (== DISK_TYPE) and written to <dir>/<id>/; we
# then hoist that file to the flat ptool-partitions.xml the rest of the flow
# expects.
if [ -e "${BOARD_YAML}" ]; then
    ptool_tmp="$(mktemp -d)"
    python3 -m qcom_ptool.gen_partition --board "${BOARD_YAML}" \
        -C "${ptool_tmp}" \
        -m "${partition_map}"
    cp "${ptool_tmp}/${DISK_TYPE}/partitions.xml" ptool-partitions.xml
    rm -rf "${ptool_tmp}"
else
    python3 -m qcom_ptool.gen_partition -i "${PARTITIONS_CONF}" \
        -o ptool-partitions.xml \
        -m "${partition_map}"
fi

# generate contents.xml from ptool-partitions.xml and contents.xml.in
CONTENTS="${QCOM_PTOOL}/platforms/${PLATFORM}/contents.xml.in"
if [ -e "$CONTENTS" ]; then
    python3 -m qcom_ptool.gen_contents -p ptool-partitions.xml \
        -t "$CONTENTS" \
        -b "$BUILDID" \
        -o contents.xml
fi

# generate flashing files from qcom-partitions.xml
python3 -m qcom_ptool.ptool -x ptool-partitions.xml
