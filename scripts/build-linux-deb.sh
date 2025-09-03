#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

set -eu

# git repo/branch to use
GIT_REPO="https://github.com/torvalds/linux"
GIT_BRANCH="master"
# where to clone / build
WORK_DIR="linux"
# base config to use
CONFIG="defconfig"
# flavor name
FLAVOR="upstream"

log_i() {
    echo "I: $*" >&2
}

fatal() {
    echo "F: $*" >&2
    exit 1
}

check_dependencies() {
    # needed to clone repository and generate version from latest tag/commit
    packages="git"
    # will pull gcc-aarch64-linux-gnu; should pull a native compiler on arm64
    # and a cross-compiler on other architectures
    packages="${packages} crossbuild-essential-arm64"
    # linux build-dependencies; see linux/scripts/package/mkdebian
    packages="${packages} make flex bison bc libdw-dev libelf-dev libssl-dev"
    packages="${packages} libssl-dev:arm64"
    # linux build-dependencies for debs
    packages="${packages} dpkg-dev debhelper-compat kmod python3 rsync"
    # for nproc
    packages="${packages} coreutils"

    missing=""
    for pkg in ${packages}; do
        # check if package with this name is installed
        if dpkg -l "${pkg}" 2>&1 | grep -q "^ii  ${pkg}"; then
            continue
        fi
        # otherwise, check if it's a virtual package and if some package
        # providing it is installed
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
}

get_kernel() {
    git clone --depth=1 --branch "${GIT_BRANCH}" "${GIT_REPO}" "${WORK_DIR}"
}

configure_kernel() {
    rm -vf "${WORK_DIR}/kernel/configs/local.config"
    for fragment in "$@"; do
        log_i "Adding config fragment to local.config: ${fragment}"
        touch "${WORK_DIR}/kernel/configs/local.config"
        cat "$fragment" >>"${WORK_DIR}/kernel/configs/local.config"
    done

    if [ -r "${WORK_DIR}/kernel/configs/local.config" ]; then
        make -C "${WORK_DIR}" ARCH=arm64 "${CONFIG}" local.config
    else
        make -C "${WORK_DIR}" ARCH=arm64 "${CONFIG}"
    fi
}

build_kernel() {
    # DEBUG
    sed -i '2i set -x' scripts/setlocalversion
    LOCALVERSION="-${FLAVOR}" \
        make -C "${WORK_DIR}" "-j$(nproc)" \
            ARCH=arm64 \
            CROSS_COMPILE=aarch64-linux-gnu- \
            DEB_HOST_ARCH=arm64 \
            KDEB_SOURCENAME="linux-${FLAVOR}" \
            deb-pkg
}

log_i "Checking build-dependencies"
check_dependencies

log_i "Getting Linux from repo ${GIT_REPO} and branch ${GIT_BRANCH}"
get_kernel

log_i "Configuring Linux with base config ${CONFIG} and config fragments $*"
configure_kernel "$@"

log_i "Building Linux deb"
build_kernel

