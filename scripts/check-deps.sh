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

# When the dpkg database is absent there is no package state to query. This
# happens for a chroot:false action running inside a fakemachine guest, which
# bind-mounts the host /usr (so the dpkg binary is present) but not /var (so
# the status database is not). Test the database rather than the binary and
# skip: the dependencies are expected to be present on the real host that
# invoked debos.
if [ ! -f /var/lib/dpkg/status ]; then
    echo "W: no dpkg database found; skipping host dependency check" >&2
    exit 0
fi

missing=""
for pkg in "$@"; do
    dpkg -s "$pkg" >/dev/null 2>&1 || missing="$missing $pkg"
done

if [ -z "$missing" ]; then
    exit 0
fi

if [ "$install" = true ]; then
    sudo=""
    [ "$(id -u)" -eq 0 ] || sudo="sudo"
    echo "I: refreshing APT package lists" >&2
    $sudo apt-get update
    echo "I: installing missing host packages:$missing" >&2
    $sudo apt-get install -y --no-install-recommends $missing
else
    echo "E: Missing host packages:$missing" >&2
    echo "E: Install them manually, or set -t auto_install_deps:true to install automatically" >&2
    exit 1
fi
