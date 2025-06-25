#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

set -eu

# patches in this repo/branch were submitted upstream here:
# https://lore.kernel.org/u-boot/20250410080027.208674-1-sumit.garg@kernel.org/
GIT_REPO="https://github.com/b49020/u-boot"
GIT_BRANCH="qcom-mainline"
CONFIG="qcom_defconfig"
U_BOOT_DEVICE_TREE="qcom/qrb2210-rb1"
ABOOT_BASE_ADDRESS="0x80000000"
ABOOT_PAGE_SIZE="4096"
ABOOT_OUTPUT="rb1-boot.img"
CAPSULE_OUTPUT="u-boot-cap.bin"
CABINET_OUTPUT="u-boot.cab"

log_i() {
    echo "I: $*" >&2
}

fatal() {
    echo "F: $*" >&2
    exit 1
}

# needed to clone repository
packages="git"
# will pull gcc-aarch64-linux-gnu; should pull a native compiler on arm64 and
# a cross-compiler on other architectures
packages="${packages} crossbuild-essential-arm64"
# u-boot build-dependencies
packages="${packages} make bison flex bc libssl-dev gnutls-dev xxd"
# for nproc
packages="${packages} coreutils"
# needed to pack resulting u-boot binary into an Android boot image
packages="${packages} gzip mkbootimg"
# needed to build fwupd cabinet archive for EFI firmware capsule updates
packages="${packages} fwupd"

log_i "Checking build-dependencies ($packages)"
missing=""
for pkg in ${packages}; do
    # check if package with this name is installed
    if dpkg -l "${pkg}" 2>&1 | grep -q "^ii  ${pkg}"; then
        continue
    fi
    # otherwise, check if it's a virtual package and if some package providing
    # it is installed
    providers="$(apt-cache showpkg "${pkg}" |
                     sed -e '1,/^Reverse Provides: *$/ d' -e 's/ .*$//' |
                     sort -u)"
    provider_found="no"
    for provider in ${providers}; do
        if dpkg -l "${provider}" 2>&1 | grep -q "^ii  ${provider}"; then
            provider_found="yes"
            break
        fi
    done
    if [ "${provider_found}" = yes ]; then
        continue
    fi
    missing="${missing} ${pkg}"
done
if [ -n "${missing}" ]; then
    fatal "Missing build-dependencies: ${missing}"
fi

log_i "Cloning U-Boot (${GIT_REPO}:${GIT_BRANCH})"
git clone --depth=1 --branch "${GIT_BRANCH}" "${GIT_REPO}" u-boot

cd u-boot

log_i "Configuring U-Boot (${CONFIG})"
make "${CONFIG}"

log_i "Building U-Boot (with device tree ${U_BOOT_DEVICE_TREE})"
make "-j$(nproc)" \
    CROSS_COMPILE=aarch64-linux-gnu- DEVICE_TREE="${U_BOOT_DEVICE_TREE}"

log_i "Creating Android boot image (${ABOOT_OUTPUT})"
gzip u-boot-nodtb.bin
cat u-boot-nodtb.bin.gz \
    "dts/upstream/src/arm64/${U_BOOT_DEVICE_TREE}.dtb" \
    >u-boot-nodtb.bin.gz-dtb
# dummy empty file as we don't need a ramdisk
touch empty-ramdisk
mkbootimg --base "${ABOOT_BASE_ADDRESS}" \
    --pagesize "${ABOOT_PAGE_SIZE}" \
    --kernel u-boot-nodtb.bin.gz-dtb  \
    --cmdline "root=/dev/notreal" \
    --ramdisk empty-ramdisk \
    --output "${ABOOT_OUTPUT}"

# EFI firmware capsule and fwupd cabinet files generation. Note that currently
# only U-Boot firmware can be updated using capsule updates without support for
# dual bank (A/B) capsule updates. The next steps is to add support for dual
# bank capsule updates as well as support to update Qualcomm downstream boot
# firmware too.

# The GUID used below for U-Boot firmware can be generated dynamically via:
# $ ./tools/mkeficapsule guidgen dts/upstream/src/arm64/qcom/qrb2210-rb1.dtb UBOOT_BOOT_PARTITION
# Generating GUIDs for qcom,qrb2210-rb1 with namespace 8c9f137e-91dc-427b-b2d6-b420faebaf2a:
# UBOOT_BOOT_PARTITION: 77F90B51-588C-5EF0-AAB9-046AEB2AC8C5

./tools/mkeficapsule \
    --index 1 \
    --instance 0 \
    --guid 77F90B51-588C-5EF0-AAB9-046AEB2AC8C5 \
    "${ABOOT_OUTPUT}" \
    "${CAPSULE_OUTPUT}"
rm -f "${CABINET_OUTPUT}"
fwupdtool build-cabinet \
    "${CABINET_OUTPUT}" \
    "${CAPSULE_OUTPUT}" \
    board/qualcomm/rb1/u-boot-cap.metainfo.xml
