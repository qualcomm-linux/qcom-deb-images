#!/usr/bin/env bash
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

set -euo pipefail

USAGE="Usage: $0 PRESEED.cfg MODULE_RULES KERNEL_DIR INSTALLER_DIR"
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

if [ "$#" -ne 4 ]; then
    echo "$USAGE" >&2
    exit 1
fi

# The preseed, added to the installer initrds as /preseed.cfg, and the rules
# for trim-modules.sh: modules to leave out.
PRESEED=$(realpath "$1")
MODULE_RULES=$(realpath "$2")

if [ ! -f "$PRESEED" ]; then
    echo "Missing preseed: $PRESEED" >&2
    exit 1
fi

if [ ! -f "$MODULE_RULES" ]; then
    echo "Missing module rules: $MODULE_RULES" >&2
    exit 1
fi

# The custom kernel's package, unpacked.
KERNEL=$(realpath "$3")
INSTALL=$(realpath "$4")

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this script as root" >&2
    exit 1
fi

if [ ! -d "$KERNEL" ]; then
    echo "Missing kernel directory: $KERNEL" >&2
    exit 1
fi

if [ ! -d "$INSTALL" ]; then
    echo "Missing $INSTALL; run lb installer first" >&2
    exit 1
fi

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# Write a data-less newc cpio entry owned by root: NAME MODE NLINK
# RDEVMAJOR RDEVMINOR, MODE in decimal including the file type bits.
newc_entry() {
    local namesize=$((${#1} + 1))

    printf '070701%08X%08X%08X%08X%08X%08X%08X%08X%08X%08X%08X%08X%08X' \
        0 "$2" 0 0 "$3" 0 0 0 0 "$4" "$5" "$namesize" 0
    printf '%s\0' "$1"
    # Pad the 110-byte header and the name to a multiple of 4 bytes.
    head -c $(((4 - (110 + namesize) % 4) % 4)) /dev/zero
}

# Support both traditional and merged-/usr kernel packages.
MODULES="$KERNEL/lib/modules"

if [ ! -d "$MODULES" ]; then
    MODULES="$KERNEL/usr/lib/modules"
fi

if [ ! -d "$MODULES" ]; then
    echo "Kernel package(s) contain no modules" >&2
    exit 1
fi

mapfile -t VERSIONS < <(
    find "$MODULES" \
        -mindepth 1 -maxdepth 1 \
        -type d -printf '%f\n'
)

if [ "${#VERSIONS[@]}" -ne 1 ]; then
    echo "Expected exactly one kernel release" >&2
    exit 1
fi

KVER="${VERSIONS[0]}"
VMLINUX="$KERNEL/boot/vmlinuz-$KVER"

if [ ! -f "$VMLINUX" ]; then
    echo "Missing kernel image: $VMLINUX" >&2
    exit 1
fi

echo "Custom kernel: $KVER"

# Kernel packages don't necessarily ship modules.dep (their postinst may
# generate it), and trim-modules.sh needs it. depmod looks in lib/modules, so give a
# merged-/usr tree the usual /lib symlink.
if [ ! -e "$KERNEL/lib" ]; then
    ln -s usr/lib "$KERNEL/lib"
fi

depmod -b "$KERNEL" "$KVER"

# Trim the modules once here, rather than in each initrd.
echo "Trimming modules with $MODULE_RULES"
"$SCRIPT_DIR/trim-modules.sh" "$MODULES/$KVER" "$MODULE_RULES"

COUNT=0

for INITRD in \
    "$INSTALL/initrd.gz" \
    "$INSTALL/gtk/initrd.gz"
do
    if [ ! -f "$INITRD" ]; then
        echo "Missing $INITRD; run lb installer first" >&2
        exit 1
    fi

    DIR=$(dirname "$INITRD")
    ROOT="$WORK/initrd-$COUNT"

    mkdir -p "$ROOT"

    echo "Processing $INITRD"

    # The initrd's device nodes (dev/console, dev/null), as "NAME MODE
    # MAJOR MINOR" lines. Creating them needs CAP_MKNOD, which a rootless
    # (e.g. podman) container lacks even with --privileged, and fakeroot
    # hung in the CI container; so leave them out when unpacking and add
    # them back to the archive when repacking.
    NODES="$WORK/nodes-$COUNT"
    gzip -dc "$INITRD" |
        cpio -itv --quiet |
        awk '$1 ~ /^[cb]/ {
            mode = substr($1, 1, 1) == "c" ? 8192 : 24576
            for (i = 2; i <= 10; i++)
                if (substr($1, i, 1) != "-")
                    mode += 2 ^ (10 - i)
            sub(",", "", $5)
            print $NF, mode, $5, $6
        }' > "$NODES"

    EXCLUDE=()
    if [ -s "$NODES" ]; then
        cut -d ' ' -f 1 "$NODES" > "$NODES.names"
        EXCLUDE=(--nonmatching --pattern-file="$NODES.names")
    fi

    # Extract the existing Debian Installer userspace.
    echo "  Unpacking"
    (
        cd "$ROOT"

        gzip -dc "$INITRD" |
            cpio -idmu --quiet --no-absolute-filenames "${EXCLUDE[@]}"
    )

    # Handle traditional and merged-/usr installer initrds.
    if [ -L "$ROOT/lib" ]; then
        if [ "$(readlink "$ROOT/lib")" != "usr/lib" ]; then
            echo "Unsupported /lib symlink" >&2
            exit 1
        fi

        MODULE_DEST="$ROOT/usr/lib/modules"
    else
        MODULE_DEST="$ROOT/lib/modules"
    fi

    # Remove modules belonging to the original d-i kernel.
    echo "  Replacing modules"
    rm -rf "$MODULE_DEST"

    # Include the custom kernel's (trimmed) modules.
    mkdir -p "$MODULE_DEST"

    cp -a \
        "$MODULES/$KVER" \
        "$MODULE_DEST/"

    # Generate module dependency metadata.
    depmod -b "$ROOT" "$KVER"

    # d-i loads /preseed.cfg from the initrd before anything else runs.
    install -m 0644 "$PRESEED" "$ROOT/preseed.cfg"

    # Rebuild the installer initrd, with everything owned by root whoever
    # owns it on disk (e.g. a checkout owned by a non-root user). xz rather
    # than d-i's gzip, to shrink the initrd GRUB has to load. The kernel
    # finds the format from the data, so the file keeps its initrd.gz name
    # (which grub.cfg uses); its xz decompressor needs --check=crc32. One
    # thread, so xz compresses everything as one block.
    echo "  Repacking"
    # The device nodes go in a second cpio archive after the first, which
    # has created dev/: the kernel unpacks concatenated archives in turn.
    (
        cd "$ROOT"

        {
            find . -print0 |
                cpio --null -o -H newc --quiet -R 0:0

            if [ -s "$NODES" ]; then
                while read -r NAME MODE MAJOR MINOR; do
                    newc_entry "$NAME" "$MODE" 1 "$MAJOR" "$MINOR"
                done < "$NODES"
                newc_entry TRAILER!!! 0 1 0 0
            fi
        } | xz -9 --check=crc32 --threads=1
    ) > "$INITRD.new"

    mv "$INITRD.new" "$INITRD"

    # Replace the kernel used by this installer entry.
    cp "$VMLINUX" "$DIR/vmlinuz"

    COUNT=$((COUNT + 1))
done

echo
echo "Replaced $COUNT installer kernel/initrd pair(s)"
echo "Kernel release: $KVER"
