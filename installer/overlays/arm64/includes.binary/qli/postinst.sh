#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# Run by d-i's preseed/late_command, after GRUB is installed, to get the
# installed system booting on the Qualcomm laptops. Everything else comes
# natively from d-i and the pool; the parts in postinst.d/ only install the
# files from the ISO's /qli/ that no package ships, then rebuild what
# depends on them.
#
# The parts are sourced in lexical order, so they share log(), install_file()
# and set -e, and need no exec bit.
set -e

TARGET=${1:-/target}
SRC=/cdrom/qli

log() {
    echo "postinst: $*"
    logger -t postinst "$*" 2>/dev/null || true
}

# Copy <src> to /target/<dest> with <mode>; busybox has no install.
install_file() {
    if [ ! -f "$1" ]; then
        log "missing $1"
        exit 1
    fi

    log "installing $2"
    mkdir -p "$(dirname "$TARGET$2")"
    cp "$1" "$TARGET$2"
    chmod "$3" "$TARGET$2"
}

for part in "$SRC"/postinst.d/*; do
    [ -f "$part" ] || continue
    log "running ${part##*/}"
    # shellcheck source=/dev/null
    . "$part"
done
