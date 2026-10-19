#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# Install the latest Grype vulnerability database, through a cache shared by
# all jobs (e.g. on EFS).
#
# Usage: grype-db-cache.sh [--read-only] GRYPE CACHE_ROOT
#
# GRYPE is the path to the Grype executable. The database is installed in
# GRYPE_DB_CACHE_DIR, which must be set, and where later Grype invocations
# will find it.
#
# Installing a new database downloads a ~200MB archive, which only takes
# seconds, then decompresses and indexes it into a ~3GB SQLite file, which
# takes minutes. The database is only rebuilt upstream about once a day, so
# installed databases are kept as zstd compressed tarballs (~400MB) in
# CACHE_ROOT/<grype version>/<built date>.tar.zst, and unpacked from there in
# seconds; keying by the Grype version as well avoids a newer Grype having to
# re-index ("rehydrate") its copy of a database installed by an older one,
# every time.
#
# With --read-only the cache is only read: nothing is created, published or
# pruned, e.g. for jobs not running from the main branch. When the latest
# database isn't in the cache yet, the most recent entry published less than
# max_age_minutes ago is used instead, rather than installing the latest one
# locally, which would take minutes.
#
# A missing or broken cache falls back to a plain "grype db update". Run
# Grype with GRYPE_DB_AUTO_UPDATE=false afterwards, otherwise it may decide
# to download the database again when scanning.
#
# Older entries are pruned here. Removing the whole CACHE_ROOT at any time is
# safe, the next run without --read-only then installs and publishes the
# database again; e.g. efs-maintenance.yml removes it by age, which also
# cleans it up once this script stops running.

set -eu

read_only=0
if [ "${1:-}" = "--read-only" ]; then
    read_only=1
    shift
fi

grype="$1"
cache_root="$2"

# cache entries older than this many minutes, other than the latest one, are
# removed; databases are rebuilt upstream about once a day, and 2 days leave
# plenty of time for jobs still unpacking an older entry to finish
max_age_minutes=2880

: "${GRYPE_DB_CACHE_DIR:?must be set}"
export GRYPE_DB_CACHE_DIR

log() {
    echo "grype-db-cache: $*" >&2
}

# print the value of a top-level key of the JSON document on stdin, or of a key
# of an object nested one level below it
json_get() {
    python3 -c '
import json, sys
value = json.load(sys.stdin)
for key in sys.argv[1:]:
    value = (value or {}).get(key)
print(value or "")
' "$@"
}

# convert an RFC 3339 timestamp (e.g. 2026-10-19T06:39:15Z) into a compact
# form for use in file names (e.g. 20261019T063915Z)
compact_date() {
    echo "$1" | tr -d ':-'
}

update_locally() {
    log "updating the database from upstream"
    rm -rf "${GRYPE_DB_CACHE_DIR}"
    "$grype" db update -v
}

# print the built date of the installed database, or nothing when there is no
# valid database installed; "db status" checks the integrity of the database
# against its checksum, so a truncated copy is caught here
installed_built() {
    if status="$("$grype" db status -o json)"; then
        compact_date "$(echo "$status" | json_get built)"
    fi
}

# remove the cache entries older than max_age_minutes, other than the one
# given, i.e. older databases, leftovers of interrupted publications and the
# entries of other Grype versions, then the directories left empty; never fail
# the job on errors when pruning
prune() {
    [ "$read_only" -eq 0 ] || return 0
    find "${cache_root}" -mindepth 2 -maxdepth 2 -type f \
        -mmin "+${max_age_minutes}" ! -path "$1" -delete || true
    find "${cache_root}" -mindepth 1 -maxdepth 1 -type d -empty \
        -mmin "+${max_age_minutes}" -delete || true
}

# print the built date of the most recent database in the cache, or nothing
# when there is none; extra arguments are passed to find, e.g. to filter by age
newest_cached() {
    find "${version_dir}" -mindepth 1 -maxdepth 1 -type f \
        -name '[0-9]*.tar.zst' "$@" -printf '%f\n' |
        sort | tail -n 1 | sed 's/\.tar\.zst$//'
}

# install the database cached as the given built date; fails when the entry
# is missing or unusable
unpack() {
    entry="${version_dir}/$1.tar.zst"
    [ -f "${entry}" ] || return 1
    log "unpacking database $1 from ${entry}"
    rm -rf "${GRYPE_DB_CACHE_DIR}"
    mkdir -p "${GRYPE_DB_CACHE_DIR}"
    if tar -C "${GRYPE_DB_CACHE_DIR}" --zstd -xf "${entry}" &&
        [ "$(installed_built)" = "$1" ]; then
        return 0
    fi
    log "WARNING: cached database ${entry} is unusable; ignoring it"
    return 1
}

version="$("$grype" version -o json | json_get version)"
version_dir="${cache_root}/${version}"

if [ "$read_only" -eq 1 ]; then
    if [ ! -d "${version_dir}" ]; then
        log "no cache in ${version_dir}; not using the cache"
        update_locally
        exit 0
    fi
elif ! mkdir -p "${version_dir}" 2>/dev/null; then
    log "WARNING: ${cache_root} is not writable; not using the cache"
    update_locally
    exit 0
fi

# ask upstream for the built date of its latest database; this only fetches a
# small listing file. "db check" exits with 100 when an update is available,
# which is always the case when querying with an empty directory
check_dir="$(mktemp -d)"
rc=0
check="$(GRYPE_DB_CACHE_DIR="${check_dir}" "$grype" db check -o json)" || rc=$?
rmdir "${check_dir}" 2>/dev/null || true
latest=""
if [ "$rc" -eq 0 ] || [ "$rc" -eq 100 ]; then
    latest="$(compact_date "$(echo "$check" | json_get candidateDB built)")"
fi
if [ -z "$latest" ]; then
    # the upstream listing is unavailable: fall back to the most recent
    # database in the cache; Grype refuses databases older than 5 days
    latest="$(newest_cached)"
    log "WARNING: couldn't check for the latest database; using ${latest:-none}"
    if [ -z "$latest" ]; then
        update_locally
        exit 0
    fi
fi

if unpack "$latest"; then
    prune "${entry}"
    exit 0
fi

if [ "$read_only" -eq 1 ]; then
    # the latest database isn't published yet; use a recent one rather than
    # installing it locally
    recent="$(newest_cached -mmin "-${max_age_minutes}")"
    if [ -n "$recent" ] && [ "$recent" != "$latest" ]; then
        log "database ${latest} isn't cached yet; using ${recent} instead"
        if unpack "$recent"; then
            exit 0
        fi
    fi
fi

update_locally
built="$(installed_built)"
if [ -z "$built" ]; then
    log "ERROR: no valid database after updating"
    exit 1
fi
[ "$read_only" -eq 0 ] || exit 0

# publish the database; write a temporary file next to the entry and rename
# it, so that other jobs never see a partial entry. when another job published
# the same database in the meantime, its entry is simply replaced
entry="${version_dir}/${built}.tar.zst"
tmp="${version_dir}/.tmp.${built}.$$.tar.zst"
if [ ! -f "${entry}" ]; then
    log "publishing database ${built} to ${entry}"
    if ! { tar -C "${GRYPE_DB_CACHE_DIR}" -I 'zstd -T0 -6' -cf "${tmp}" . &&
            mv -f "${tmp}" "${entry}"; }; then
        log "WARNING: couldn't publish ${entry}"
    fi
    rm -f "${tmp}"
fi

prune "${entry}"
