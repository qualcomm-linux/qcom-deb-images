#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

set -eu

GIT_REPO="https://github.com/qualcomm-linux/u-boot"
GIT_BRANCH="qcom-next"
CONFIG="qcom_defconfig"
U_BOOT_DEVICE_TREE="qcom/qrb2210-rb1"
ABOOT_BASE_ADDRESS="0x80000000"
ABOOT_PAGE_SIZE="4096"
ABOOT_OUTPUT="rb1-boot.img"
CAPSULE_OUTPUT="u-boot-cap.bin"
CABINET_OUTPUT="u-boot.cab"
METAINFO_TEMPLATE="$(cd "$(dirname "$0")" && pwd)/u-boot-rb1.metainfo.xml.in"

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
packages="${packages} make bison flex bc libssl-dev gnutls-dev uuid-dev xxd"
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

# The GUID used below for U-Boot firmware is not derived from the device tree
# but fixed for all boards; see QCOM_UBOOT_IMAGE_TYPE_GUID in
# arch/arm/mach-snapdragon/capsule_update.c.
CAPSULE_GUID="400ffdcd-22e0-47e7-9a23-f16ed9382388"

./tools/mkeficapsule \
    --index 1 \
    --instance 0 \
    --guid "${CAPSULE_GUID}" \
    "${ABOOT_OUTPUT}" \
    "${CAPSULE_OUTPUT}"

# generate the fwupd metadata from a template here rather than relying on the
# U-Boot tree shipping it for RB1
log_i "Creating fwupd cabinet archive (${CABINET_OUTPUT})"
version="$(make -s ubootversion)"
date="$(git log -1 --format=%cs)"
sed -e "s|@CAPSULE_GUID@|${CAPSULE_GUID}|g" \
    -e "s|@GIT_REPO@|${GIT_REPO}|g" \
    -e "s|@VERSION@|${version}|g" \
    -e "s|@DATE@|${date}|g" \
    -e "s|@CAPSULE_OUTPUT@|${CAPSULE_OUTPUT}|g" \
    "${METAINFO_TEMPLATE}" >u-boot-cap.metainfo.xml
rm -f "${CABINET_OUTPUT}"
fwupdtool build-cabinet \
    "${CABINET_OUTPUT}" \
    "${CAPSULE_OUTPUT}" \
    u-boot-cap.metainfo.xml
