#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

set -eu

# git repo/branch to use
GIT_REPO="https://github.com/torvalds/linux"
GIT_BRANCH="master"
# base config to use
CONFIG="defconfig"

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
# linux build-dependencies; see linux/scripts/package/mkdebian
packages="${packages} make flex bison bc libdw-dev libelf-dev libssl-dev"
packages="${packages} libssl-dev:arm64"
# linux build-dependencies for debs
packages="${packages} dpkg-dev debhelper-compat kmod python3 rsync"
# for nproc
packages="${packages} coreutils"

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

log_i "Cloning Linux (${GIT_REPO}:${GIT_BRANCH})"
git clone --depth=1 --branch "${GIT_BRANCH}" "${GIT_REPO}" linux

log_i "Configuring Linux (base config: ${CONFIG})"
rm -vf linux/kernel/configs/local.config
for fragment in "$@"; do
    log_i "Adding config fragment to local.config: ${fragment}"
    touch linux/kernel/configs/local.config
    cat "$fragment" >>linux/kernel/configs/local.config
done

# only change working directory after having read config fragments passed on
# the command-line as these might be relative pathnames
cd linux

if [ -r kernel/configs/local.config ]; then
    make ARCH=arm64 "${CONFIG}" local.config
else
    make ARCH=arm64 "${CONFIG}"
fi

log_i "Building Linux deb"
# TODO: build other packages?
make "-j$(nproc)" \
    ARCH=arm64 DEB_HOST_ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
    bindeb-pkg

