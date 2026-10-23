#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# scripts/lib/fetch-kernel-deps.sh <build_dir> <suite> <arch> KERNEL.deb...
#
# Downloads everything the kernel packages depend on to
# <build_dir>/kernel-deps-apt/debs/, for build.sh to add to the pool.
# base-installer installs the kernel from the pool, before apt-setup adds
# backports on the target, so the pool needs anything newer the kernel
# depends on: e.g. the trixie-backports kernel needs linux-base from
# trixie-backports.
#
# The dependencies are resolved against an empty system, so this also
# downloads base packages (libc6, kmod etc.) that the pool already has.
# They're the same versions as the pool's, from the same mirror, so adding
# them again is harmless; picking out only the missing ones isn't worth it.
# Uses a private apt state/cache dir and sources.list, like
# fetch-stubble.sh.
set -eu

BUILD_DIR=$1
SUITE=$2
ARCH=$3
shift 3

APT_DIR="$BUILD_DIR/kernel-deps-apt"
rm -rf "$APT_DIR"
mkdir -p "$APT_DIR/lists/partial" "$APT_DIR/cache" "$APT_DIR/sources.list.d" \
    "$APT_DIR/preferences.d" "$APT_DIR/debs/partial"
: > "$APT_DIR/status"
: > "$APT_DIR/preferences"

cat > "$APT_DIR/sources.list" <<EOT
deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] http://deb.debian.org/debian $SUITE main
EOT

# Only stable suites have backports. Prefer linux-base from backports, like
# the target does (see target-apt.sh), but keep everything else at the
# suite's versions, which the pool has.
if [ "$SUITE" = "trixie" ]; then
    cat >> "$APT_DIR/sources.list" <<EOT
deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] http://deb.debian.org/debian $SUITE-backports main
EOT
    cat > "$APT_DIR/preferences" <<EOT
Package: src:linux-base:any
Pin: release n=$SUITE-backports
Pin-Priority: 900
EOT
fi

cat > "$APT_DIR/apt.conf" <<EOT
Dir::State "$APT_DIR";
Dir::State::status "$APT_DIR/status";
Dir::Cache "$APT_DIR/cache";
Dir::Cache::archives "$APT_DIR/debs";
Dir::Etc::sourcelist "$APT_DIR/sources.list";
Dir::Etc::sourceparts "$APT_DIR/sources.list.d";
Dir::Etc::preferences "$APT_DIR/preferences";
Dir::Etc::preferencesparts "$APT_DIR/preferences.d";
APT::Architecture "$ARCH";
APT::Architectures "$ARCH";
EOT

APT_CONFIG="$APT_DIR/apt.conf" apt-get update
# The kernel debs are installed from their files, so only their
# dependencies are downloaded. initramfs-tools, which base-installer
# installs, satisfies the kernel's "dracut | initramfs-tools" dependency;
# otherwise apt picks dracut and its dependencies.
APT_CONFIG="$APT_DIR/apt.conf" apt-get install --download-only \
    --no-install-recommends -y initramfs-tools "$@"
