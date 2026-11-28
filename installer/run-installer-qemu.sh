#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# Boot an installer image in QEMU on an arm64 host with KVM, e.g.:
#
#   installer/run-installer-qemu.sh installer/build/trixie-qcom-next/*.iso
#
# The image is attached as a USB stick, in snapshot mode so it isn't
# changed, and a virtio disk to install to, which is created if missing
# and kept between runs, along with the UEFI variables next to it. Boot
# the installed system by running again without the image (--no-image).
#
# Needs qemu-system-arm, qemu-utils and qemu-efi-aarch64. The host needs
# KVM: an arm64 machine with /dev/kvm.
set -eu

IMAGE=""
DISK="installer/build/qemu-disk.qcow2"
DISK_SIZE="32G"
MEMORY="4G"
CPUS="4"
NO_IMAGE=0
USAGE="Usage: $0 [--disk FILE] [--disk-size SIZE] [--memory SIZE] [--cpus N] IMAGE.iso | --no-image"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --disk)
            DISK="${2:?Missing value for --disk}"
            shift 2
            ;;
        --disk-size)
            DISK_SIZE="${2:?Missing value for --disk-size}"
            shift 2
            ;;
        --memory)
            MEMORY="${2:?Missing value for --memory}"
            shift 2
            ;;
        --cpus)
            CPUS="${2:?Missing value for --cpus}"
            shift 2
            ;;
        --no-image)
            NO_IMAGE=1
            shift
            ;;
        -h|--help)
            echo "$USAGE"
            exit 0
            ;;
        -*)
            echo "$USAGE" >&2
            exit 1
            ;;
        *)
            [ -z "$IMAGE" ] || { echo "$USAGE" >&2; exit 1; }
            IMAGE="$1"
            shift
            ;;
    esac
done

# Exactly one of IMAGE and --no-image.
if [ "$NO_IMAGE" = 1 ]; then
    [ -z "$IMAGE" ] || { echo "$USAGE" >&2; exit 1; }
else
    [ -n "$IMAGE" ] || { echo "$USAGE" >&2; exit 1; }
fi

if [ -n "$IMAGE" ] && [ ! -f "$IMAGE" ]; then
    echo "Missing image: $IMAGE" >&2
    exit 1
fi

if [ "$(uname -m)" != aarch64 ]; then
    echo "This needs an arm64 host, for KVM" >&2
    exit 1
fi

if [ ! -w /dev/kvm ]; then
    echo "/dev/kvm is missing or not writable (are you in the kvm group?)" >&2
    exit 1
fi

# The image isn't signed for Secure Boot, so use firmware without it.
FIRMWARE_DIR=/usr/share/AAVMF
FIRMWARE="$FIRMWARE_DIR/AAVMF_CODE.no-secboot.fd"
[ -f "$FIRMWARE" ] || FIRMWARE="$FIRMWARE_DIR/AAVMF_CODE.fd"
if [ ! -f "$FIRMWARE" ]; then
    echo "Missing UEFI firmware in $FIRMWARE_DIR (install qemu-efi-aarch64)" >&2
    exit 1
fi

mkdir -p "$(dirname "$DISK")"
if [ ! -e "$DISK" ]; then
    qemu-img create -f qcow2 "$DISK" "$DISK_SIZE"
fi

# The UEFI variables, kept with the disk so its boot entries survive.
VARS="${DISK%.qcow2}-vars.fd"
if [ ! -e "$VARS" ]; then
    cp "$FIRMWARE_DIR/AAVMF_VARS.fd" "$VARS"
fi

# Boot with a DT, not ACPI: QEMU's SPCR table makes the serial port the
# preferred console over the image's console=tty0, so d-i would run on
# serial instead of the screen.
set -- \
    -machine virt,acpi=off -cpu host -accel kvm \
    -smp "$CPUS" -m "$MEMORY" \
    -drive if=pflash,format=raw,readonly=on,file="$FIRMWARE" \
    -drive if=pflash,format=raw,file="$VARS" \
    -device virtio-gpu-pci \
    -device qemu-xhci \
    -device usb-kbd \
    -device usb-tablet \
    -drive if=none,id=disk,format=qcow2,file="$DISK" \
    -device virtio-blk-pci,drive=disk,bootindex=1 \
    -netdev user,id=net \
    -device virtio-net-pci,netdev=net \
    -serial mon:stdio

if [ -n "$IMAGE" ]; then
    set -- "$@" \
        -drive if=none,id=stick,format=raw,snapshot=on,file="$IMAGE" \
        -device usb-storage,drive=stick,bootindex=0
fi

exec qemu-system-aarch64 "$@"
