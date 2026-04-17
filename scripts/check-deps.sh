#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause
#
# Check whether a list of packages is installed; optionally install missing ones.
# Usage: check-deps.sh [--install] pkg1 pkg2 ...

set -eu


setup_apt() {
    [ -d /etc/apt/apt.conf.d ] && [ -f /var/lib/dpkg/status ] && return 0
    echo "check-deps: bootstrapping apt (trixie)..."
    mkdir -p /etc/apt/apt.conf.d \
                  /etc/apt/sources.list.d \
                  /etc/apt/preferences.d \
                  /etc/apt/trusted.gpg.d \
                  /var/lib/dpkg/info \
                  /var/lib/dpkg/updates \
                  /var/lib/dpkg/alternatives \
                  /var/cache/apt/archives/partial \
                  /var/log/apt
    touch /var/lib/dpkg/status /var/lib/dpkg/available

    opt=""
    for f in /usr/share/keyrings/debian-archive-keyring.gpg \
             /usr/share/keyrings/debian-archive-removed-keys.gpg; do
        [ -f "$f" ] && cp "$f" /etc/apt/trusted.gpg.d/ || opt="[trusted=yes] "
    done

    echo "deb ${opt}http://deb.debian.org/debian trixie main contrib non-free non-free-firmware" |
        tee /etc/apt/sources.list >/dev/null
    apt-get update -y
}

install=false
if [ "$1" = "--install" ]; then
    install=true
    shift
fi

missing=""
for pkg in "$@"; do
    dpkg -s "$pkg" >/dev/null 2>&1 || missing="$missing $pkg"
done

[ -z "$missing" ] && exit 0


if [ "$install" = true ]; then
    setup_apt
    apt-get install -y --no-install-recommends $missing
else
    echo "E: Missing host packages:$missing" >&2
    echo "E: Install them manually, or set -t auto_install_deps:true to install automatically" >&2
    exit 0
fi
