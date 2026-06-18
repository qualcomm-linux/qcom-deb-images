#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause
#
# Check whether a list of packages is installed; optionally install missing ones.
# Usage: check-deps.sh [--install] pkg1 pkg2 ...

set -eu

install=false
if [ "${1:-}" = "--install" ]; then
    install=true
    shift
fi

# If dpkg is not available (e.g. inside a fakemachine qemu VM), skip silently.
# Dependencies are expected to be present on the real host in that case.
[ -f /var/lib/dpkg/status ] || exit 0

missing=""
for pkg in "$@"; do
    dpkg -s "$pkg" >/dev/null 2>&1 || missing="$missing $pkg"
done

[ -z "$missing" ] && exit 0

if [ "$install" = true ]; then
    sudo=""
    [ "$(id -u)" -eq 0 ] || sudo="sudo"
    $sudo apt-get update -qq
    $sudo apt-get install -y --no-install-recommends $missing
else
    echo "E: Missing host packages:$missing" >&2
    echo "E: Install them manually, or set -t auto_install_deps:true to install automatically" >&2
    exit 1
fi
