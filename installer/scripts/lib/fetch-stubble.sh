#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# scripts/lib/fetch-stubble.sh <build_dir> <suite>
#
# Downloads stubble for arm64 and everything it depends on to
# <build_dir>/stubble-apt/debs/, for build.sh to add to the pool, so the
# installed system can build its own Stubble images (see
# overlays/arm64/includes.binary/qli/stubble/). On trixie, stubble is only
# in trixie-backports.
#
# The dependencies are resolved against an empty system, so this also
# downloads base packages (libc6, python3 etc.) that the pool already has.
# They're the same versions as the pool's, from the same mirror, so adding
# them again is harmless; picking out only the missing ones isn't worth it.
# Uses a private apt state/cache dir and sources.list, like
# fetch-firmware.sh.
set -eu

BUILD_DIR=$1
SUITE=$2

PACKAGE=stubble

APT_DIR="$BUILD_DIR/stubble-apt"
rm -rf "$APT_DIR"
mkdir -p "$APT_DIR/lists/partial" "$APT_DIR/cache" "$APT_DIR/sources.list.d" \
    "$APT_DIR/debs/partial"
: > "$APT_DIR/status"

cat > "$APT_DIR/sources.list" <<EOF
deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] http://deb.debian.org/debian $SUITE main
EOF

# Only stable suites have backports.
if [ "$SUITE" = "trixie" ]; then
    cat >> "$APT_DIR/sources.list" <<EOF
deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] http://deb.debian.org/debian $SUITE-backports main
EOF
    PACKAGE="stubble/$SUITE-backports"
fi

cat > "$APT_DIR/apt.conf" <<EOF
Dir::State "$APT_DIR";
Dir::State::status "$APT_DIR/status";
Dir::Cache "$APT_DIR/cache";
Dir::Cache::archives "$APT_DIR/debs";
Dir::Etc::sourcelist "$APT_DIR/sources.list";
Dir::Etc::sourceparts "$APT_DIR/sources.list.d";
APT::Architecture "arm64";
APT::Architectures "arm64";
EOF

APT_CONFIG="$APT_DIR/apt.conf" apt-get update
APT_CONFIG="$APT_DIR/apt.conf" apt-get install --download-only \
    --no-install-recommends -y "$PACKAGE"

ls "$APT_DIR"/debs/stubble_*.deb >/dev/null 2>&1 || {
    echo "fetch-stubble: stubble download failed" >&2
    exit 1
}
