#!/usr/bin/env bash
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

set -euo pipefail

USAGE="Usage: $0 [--preseed PRESEED.cfg] [--module-rules RULES] [--initrd-overlay DIR] KERNEL_DIR INSTALLER_DIR [FIRMWARE_DIR]"
PRESEED=""
MODULE_RULES=""
INITRD_OVERLAY=""
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

while [ "$#" -gt 0 ]; do
    case "$1" in
        # Optional preseed, added to the installer initrds as /preseed.cfg.
        --preseed)
            PRESEED=$(realpath "${2:?$USAGE}")
            shift 2

            if [ ! -f "$PRESEED" ]; then
                echo "Missing preseed: $PRESEED" >&2
                exit 1
            fi
            ;;
        # Optional rules for lib/trim-modules.sh: modules to leave out.
        --module-rules)
            MODULE_RULES=$(realpath "${2:?$USAGE}")
            shift 2

            if [ ! -f "$MODULE_RULES" ]; then
                echo "Missing module rules: $MODULE_RULES" >&2
                exit 1
            fi
            ;;
        # Optional tree of extra files, copied over the installer initrds'
        # root.
        --initrd-overlay)
            INITRD_OVERLAY=$(realpath "${2:?$USAGE}")
            shift 2

            if [ ! -d "$INITRD_OVERLAY" ]; then
                echo "Missing initrd overlay: $INITRD_OVERLAY" >&2
                exit 1
            fi
            ;;
        *)
            break
            ;;
    esac
done

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
    echo "$USAGE" >&2
    exit 1
fi

# The custom kernel's package(s), unpacked into one tree: Debian splits its
# kernels into linux-image, linux-base, linux-binary and linux-modules
# packages.
KERNEL=$(realpath "$1")
INSTALL=$(realpath "$2")
FIRMWARE=""

# Optional /lib/firmware-style tree to add to the installer initrds.
# TODO: use a hook like in overlays/arm64/includes.binary/qli/qcom-laptop-postinst.sh
# TODO: install firmware from apt, not the fetch-firmware script...
# TODO: make the list of firmware minimal
if [ "$#" -eq 3 ]; then
    FIRMWARE=$(realpath "$3")

    if [ ! -d "$FIRMWARE" ]; then
        echo "Missing firmware directory: $FIRMWARE" >&2
        exit 1
    fi
fi

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

# pigz compresses on every core; gzip's output is the same format.
GZ=gzip
if command -v pigz >/dev/null; then
    GZ=pigz
fi

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
# Debian's split kernel packages ship the image next to the modules.
VMLINUX="$MODULES/$KVER/vmlinuz"

if [ ! -f "$VMLINUX" ]; then
    VMLINUX="$KERNEL/boot/vmlinuz-$KVER"
fi

if [ ! -f "$VMLINUX" ]; then
    echo "Missing kernel image: $VMLINUX" >&2
    exit 1
fi

echo "Custom kernel: $KVER"

# Debian's split kernel packages don't ship modules.dep (their postinst
# generates it), and lib/trim-modules.sh needs it. depmod looks in
# lib/modules, so give a merged-/usr tree the usual /lib symlink.
if [ ! -e "$KERNEL/lib" ]; then
    ln -s usr/lib "$KERNEL/lib"
fi

depmod -b "$KERNEL" "$KVER"

# Trim the modules once here, rather than in each initrd.
if [ -n "$MODULE_RULES" ]; then
    echo "Trimming modules with $MODULE_RULES"
    "$SCRIPT_DIR/lib/trim-modules.sh" "$MODULES/$KVER" "$MODULE_RULES"
fi

COUNT=0

for INITRD in \
    "$INSTALL/initrd.gz" \
    "$INSTALL/gtk/initrd.gz"
do
    [ -f "$INITRD" ] || continue

    DIR=$(dirname "$INITRD")
    ROOT="$WORK/initrd-$COUNT"

    mkdir -p "$ROOT"

    echo "Processing $INITRD"

    # Extract the existing Debian Installer userspace. This runs as root, so
    # cpio creates the initrd's device nodes for real (fakeroot isn't needed,
    # and hung in the CI container).
    echo "  Unpacking"
    (
        cd "$ROOT"

        "$GZ" -dc "$INITRD" |
            cpio -idmu --quiet --no-absolute-filenames
    )

    # Handle traditional and merged-/usr installer initrds.
    if [ -L "$ROOT/lib" ]; then
        if [ "$(readlink "$ROOT/lib")" != "usr/lib" ]; then
            echo "Unsupported /lib symlink" >&2
            exit 1
        fi

        MODULE_DEST="$ROOT/usr/lib/modules"
        FIRMWARE_DEST="$ROOT/usr/lib/firmware"
    else
        MODULE_DEST="$ROOT/lib/modules"
        FIRMWARE_DEST="$ROOT/lib/firmware"
    fi

    # Remove modules belonging to the original d-i kernel.
    echo "  Replacing modules"
    rm -rf "$MODULE_DEST"

    # Include the custom kernel's (trimmed) modules.
    mkdir -p "$MODULE_DEST"

    cp -a \
        "$MODULES/$KVER" \
        "$MODULE_DEST/"

    # The initrd needs neither the kernel image nor the DTBs that Debian's
    # split kernel packages ship next to the modules.
    rm -rf \
        "$MODULE_DEST/$KVER/vmlinuz" \
        "$MODULE_DEST/$KVER/dtb"

    # Generate module dependency metadata.
    depmod -b "$ROOT" "$KVER"

    # Add extra firmware (e.g. ath12k wifi) the custom kernel needs.
    if [ -n "$FIRMWARE" ]; then
        echo "  Adding firmware"
        mkdir -p "$FIRMWARE_DEST"
        cp -a "$FIRMWARE/." "$FIRMWARE_DEST/"
    fi

    # d-i loads /preseed.cfg from the initrd before anything else runs.
    if [ -n "$PRESEED" ]; then
        install -m 0644 "$PRESEED" "$ROOT/preseed.cfg"
    fi

    # Add extra files (e.g. d-i startup scripts). Drop group/other write, so
    # the checkout's umask (e.g. 0002) doesn't leak into the initrd.
    if [ -n "$INITRD_OVERLAY" ]; then
        echo "  Adding initrd overlay"
        cp -a --no-preserve=ownership "$INITRD_OVERLAY/." "$ROOT/"
        (cd "$INITRD_OVERLAY" && find . -mindepth 1 -print0) |
            (cd "$ROOT" && xargs -0 chmod go-w)
    fi

    # Rebuild the installer initrd, with everything owned by root whoever
    # owns it on disk (e.g. a checkout owned by a non-root user).
    echo "  Repacking"
    (
        cd "$ROOT"

        find . -print0 |
            cpio --null -o -H newc --quiet -R 0:0 |
            "$GZ" -9
    ) > "$INITRD.new"

    mv "$INITRD.new" "$INITRD"

    # Replace the kernel used by this installer entry.
    cp "$VMLINUX" "$DIR/vmlinuz"

    COUNT=$((COUNT + 1))
done

if [ "$COUNT" -eq 0 ]; then
    echo "No supported d-i initrd found" >&2
    exit 1
fi

echo
echo "Replaced $COUNT installer kernel/initrd pair(s)"
echo "Kernel release: $KVER"
