#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# scripts/lib/fetch-firmware.sh <build_dir> <suite>
#
# Fetches the Debian non-free-firmware packages the T14s needs in the
# Debian Installer initrd and extracts the relevant subtrees to
# <build_dir>/firmware/ (laid out like /lib/firmware), so
# replace-di-kernel.sh can copy them into the initrds:
#   ath12k/WCN7850     firmware-atheros   (wifi, required)
#   qcom/x1e80100      firmware-qcom-soc  (SoC remoteprocs/GPU zap, for
#                      firmware-qcom-dsp   every X1E laptop, optional)
#   qcom/gen70500_*    firmware-qcom-soc  (GPU SQE/GMU, which live outside
#                                          qcom/x1e80100, optional)
# From 20260810 (forky, trixie-backports) the ADSP/CDSP blobs moved out of
# firmware-qcom-soc into firmware-qcom-dsp; older suites (trixie) have no
# firmware-qcom-dsp, so it's only fetched when the suite has it.
# Not the whole of qcom/: it's ~460 MiB, mostly for other SoCs.
# Uses a private apt state/cache dir and sources.list, since the build
# host may not have non-free-firmware enabled. FIRMWARE_DIR=<path> uses an
# existing /lib/firmware-style tree instead (offline builds), skipping the
# network fetch entirely.
set -eu

BUILD_DIR=$1
SUITE=$2

PACKAGES="firmware-atheros firmware-qcom-soc"
REQUIRED_PATHS="ath12k/WCN7850"
OPTIONAL_PATHS="qcom/x1e80100 qcom/gen70500_sqe.fw qcom/gen70500_gmu.bin"

FIRMWARE_OUT="$BUILD_DIR/firmware"
rm -rf "$FIRMWARE_OUT"
mkdir -p "$FIRMWARE_OUT"

if [ -n "${FIRMWARE_DIR:-}" ]; then
    [ -d "$FIRMWARE_DIR" ] || { echo "fetch-firmware: FIRMWARE_DIR not found: $FIRMWARE_DIR" >&2; exit 1; }
    SRC="$FIRMWARE_DIR"
else
    # Deliberately not cached: starting from scratch every build always
    # picks up the suite's current firmware, and the packages are small next
    # to the rest of the build. apt-get update would run regardless, to find
    # the current versions. For repeated or offline builds, use FIRMWARE_DIR.
    APT_DIR="$BUILD_DIR/firmware-apt"
    rm -rf "$APT_DIR"
    mkdir -p "$APT_DIR/lists/partial" "$APT_DIR/archives/partial" "$APT_DIR/cache" \
        "$APT_DIR/sources.list.d" "$APT_DIR/debs" "$APT_DIR/root"

    cat > "$APT_DIR/sources.list" <<EOF
deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] http://deb.debian.org/debian $SUITE non-free-firmware
EOF

    cat > "$APT_DIR/apt.conf" <<EOF
Dir::State "$APT_DIR";
Dir::State::status "/var/lib/dpkg/status";
Dir::Cache "$APT_DIR/cache";
Dir::Etc::sourcelist "$APT_DIR/sources.list";
Dir::Etc::sourceparts "$APT_DIR/sources.list.d";
APT::Architectures "arm64";
EOF

    APT_CONFIG="$APT_DIR/apt.conf" apt-get update

    if APT_CONFIG="$APT_DIR/apt.conf" apt-cache show firmware-qcom-dsp >/dev/null 2>&1; then
        PACKAGES="$PACKAGES firmware-qcom-dsp"
    fi

    # shellcheck disable=SC2086 # word splitting of PACKAGES is intended
    (cd "$APT_DIR/debs" && APT_CONFIG="$APT_DIR/apt.conf" apt-get download $PACKAGES)

    for DEB in "$APT_DIR"/debs/*.deb; do
        [ -f "$DEB" ] || { echo "fetch-firmware: firmware .deb download failed" >&2; exit 1; }
        dpkg-deb -x "$DEB" "$APT_DIR/root"
    done

    SRC="$APT_DIR/root/usr/lib/firmware"
    [ -d "$SRC" ] || SRC="$APT_DIR/root/lib/firmware"
fi

copy_path() {
    mkdir -p "$FIRMWARE_OUT/$(dirname "$1")"
    cp -a "$SRC/$1" "$FIRMWARE_OUT/$1"
}

for P in $REQUIRED_PATHS; do
    [ -e "$SRC/$P" ] || { echo "fetch-firmware: $P missing from $SRC" >&2; exit 1; }
    copy_path "$P"
done

for P in $OPTIONAL_PATHS; do
    if [ -e "$SRC/$P" ]; then
        copy_path "$P"
    else
        echo "fetch-firmware: $P not found in $SRC, skipping" >&2
    fi
done

du -sh "$FIRMWARE_OUT"
