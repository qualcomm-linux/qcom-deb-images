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

# PLATFORM is "<board>/<storage-id>"; boards migrated to the single
# platforms/boards/<board>.yaml format no longer ship a per-storage
# partitions.conf, so detect that file and switch generation modes.
BOARD="${PLATFORM%%/*}"
STORAGE_ID="${PLATFORM#*/}"
BOARD_YAML="${QCOM_PTOOL}/platforms/boards/${BOARD}.yaml"
echo "BOARD_YAML=${BOARD_YAML}"

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

# generate ptool-partitions.xml: use platforms/boards/<board>.yaml via
# --board mode when it exists, otherwise fall back to the legacy
# per-storage partitions.conf
if [ -e "$BOARD_YAML" ]; then
    # --board mode has no per-storage selection; it always writes every
    # storage the board declares to its own <outdir>/<storage-id>/ dir, so
    # generate into a scratch dir and copy out just the one this call needs
    board_gen="$(mktemp -d)"
    trap 'rm -rf "${board_gen}"' EXIT
    python3 -m qcom_ptool.gen_partition \
        --board "${BOARD_YAML}" \
        -C "${board_gen}" \
        -m "${partition_map}"
    cp "${board_gen}/${STORAGE_ID}/partitions.xml" ptool-partitions.xml
else
    python3 -m qcom_ptool.gen_partition \
        -i "${QCOM_PTOOL}/platforms/${PLATFORM}/partitions.conf" \
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
