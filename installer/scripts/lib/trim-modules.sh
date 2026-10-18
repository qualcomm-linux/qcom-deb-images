#!/usr/bin/env bash
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# scripts/lib/trim-modules.sh MODULE_DIR RULES...
#
# Deletes the kernel modules the installer doesn't need from MODULE_DIR (a
# lib/modules/$KVER tree with its modules.dep), to keep the installer initrds
# small: GRUB reads them through the firmware, which is slow.
#
# Each RULES file has one shell glob per line, matched against the module's
# path relative to MODULE_DIR/kernel/ ("*" also matches "/"). A plain glob
# drops the matching modules, a "!" prefix keeps them again, and the last
# matching line wins, like .gitignore. Blank lines and "#" comments are
# ignored. Every module that a kept module depends on is also kept, so
# rules can drop whole subsystems without breaking what's left.
#
# Doesn't regenerate modules.dep etc.: run depmod afterwards.
set -euo pipefail

MODULE_DIR=${1:?Usage: $0 MODULE_DIR RULES...}
shift

RULES=()
for rules in "$@"; do
    while IFS= read -r line || [ -n "$line" ]; do
        line=${line%%#*}
        line=${line//[[:space:]]/}
        [ -n "$line" ] && RULES+=("$line")
    done < "$rules"
done

if [ ! -f "$MODULE_DIR/modules.dep" ]; then
    echo "trim-modules: missing $MODULE_DIR/modules.dep" >&2
    exit 1
fi

# modules.dep lists each module's dependencies transitively, as paths
# relative to MODULE_DIR: "kernel/a.ko: kernel/b.ko kernel/c.ko".
declare -A DEPS KEEP
while IFS= read -r line; do
    DEPS[${line%%:*}]=${line#*:}
done < "$MODULE_DIR/modules.dep"

for mod in "${!DEPS[@]}"; do
    keep=1
    for rule in "${RULES[@]}"; do
        if [ "${rule#!}" != "$rule" ]; then
            # shellcheck disable=SC2053 # the rule is a glob
            [[ ${mod#kernel/} == ${rule#!} ]] && keep=1
        else
            # shellcheck disable=SC2053
            [[ ${mod#kernel/} == $rule ]] && keep=0
        fi
    done
    [ "$keep" = 1 ] || continue

    KEEP[$mod]=1
    for dep in ${DEPS[$mod]}; do
        KEEP[$dep]=1
    done
done

dropped=0
for mod in "${!DEPS[@]}"; do
    [ -n "${KEEP[$mod]:-}" ] && continue
    rm -f "${MODULE_DIR:?}/$mod"
    dropped=$((dropped + 1))
done

# Directories left empty.
find "$MODULE_DIR/kernel" -mindepth 1 -type d -empty -delete

echo "trim-modules: kept ${#KEEP[@]} modules, dropped $dropped"
