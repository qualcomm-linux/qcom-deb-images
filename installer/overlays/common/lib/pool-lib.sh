#!/bin/bash
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# Helpers for the binary-stage hooks that prune the ISO's package pool
# (0060-0069). Sourced from config/ (overlays/*/lib/ is copied there); not
# shipped in the ISO itself. Expects cwd=binary/, like the hooks.
#
# pool_remove LABEL FILE... removes the files and the directories left
# empty, then regenerates the Packages indices and each dists/*/Release,
# which lists their hashes.

if ! command -v apt-ftparchive >/dev/null 2>&1; then
    echo "pool-lib: apt-ftparchive not found (install apt-utils)" >&2
    exit 1
fi

# Prints the pool-udeb/ files of the given udeb packages, if on the ISO.
pool_udebs() {
    local pkg f

    for pkg in "$@"; do
        for f in pool-udeb/*/*/*/"${pkg}"_*.udeb; do
            [ -f "$f" ] && echo "$f"
        done
    done
}

pool_remove() {
    local label="$1" removed_dirs
    shift

    [ "$#" -gt 0 ] || return 0

    echo "Removing $label from the pool:"
    printf '  %s\n' "${@##*/}"

    rm -f "$@"
    # Once each: a directory holding several of them is gone after the first.
    mapfile -t removed_dirs < <(printf '%s\n' "${@%/*}" | sort -u)
    rmdir --ignore-fail-on-non-empty "${removed_dirs[@]}"

    pool_regenerate_indices
}

# Regenerate an index the same way lb installer does.
_pool_regenerate_packages() {
    local pool_dir="$1" index_dir="$2"

    [ -d "$index_dir" ] || return 0

    echo "Regenerating $index_dir/Packages"
    apt-ftparchive packages "$pool_dir" > "$index_dir/Packages"
    if [ -n "${SOURCE_DATE_EPOCH:-}" ]; then
        touch -d "@$SOURCE_DATE_EPOCH" "$index_dir/Packages"
    fi
    gzip -9 -n -c "$index_dir/Packages" > "$index_dir/Packages.gz"
}

pool_regenerate_indices() {
    local dist_dir release index_dir component new_release

    for dist_dir in dists/*/; do
        dist_dir="${dist_dir%/}"
        release="$dist_dir/Release"

        [ -f "$release" ] || continue
        # e.g. a stable -> trixie symlink
        [ -L "$dist_dir" ] && continue

        for index_dir in "$dist_dir"/*/binary-*; do
            component=$(basename "$(dirname "$index_dir")")
            _pool_regenerate_packages "pool/$component" "$index_dir"
        done

        for index_dir in "$dist_dir"/*/debian-installer/binary-*; do
            component=$(basename "$(dirname "$(dirname "$index_dir")")")
            _pool_regenerate_packages "pool-udeb/$component" "$index_dir"
        done

        # Keep lb installer's header fields, and write the new Release
        # outside the dist so it doesn't hash itself.
        field() {
            sed -n "s/^$1: //p" "$release"
        }

        echo "Regenerating $release"
        new_release=$(mktemp)
        apt-ftparchive \
            -o APT::FTPArchive::Release::Origin="$(field Origin)" \
            -o APT::FTPArchive::Release::Label="$(field Label)" \
            -o APT::FTPArchive::Release::Suite="$(field Suite)" \
            -o APT::FTPArchive::Release::Version="$(field Version)" \
            -o APT::FTPArchive::Release::Codename="$(field Codename)" \
            -o APT::FTPArchive::Release::Date="$(field Date)" \
            -o APT::FTPArchive::Release::Architectures="$(field Architectures)" \
            -o APT::FTPArchive::Release::Components="$(field Components)" \
            -o APT::FTPArchive::Release::Description="$(field Description)" \
            release "$dist_dir" > "$new_release"
        rm -f "$release"
        mv "$new_release" "$release"
        chmod 0644 "$release"
        if [ -n "${SOURCE_DATE_EPOCH:-}" ]; then
            touch -d "@$SOURCE_DATE_EPOCH" "$release"
        fi
    done
}
