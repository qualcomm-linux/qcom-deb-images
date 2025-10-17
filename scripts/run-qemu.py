#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause
"""
Run Debian disk images of various sector sizes with QEMU and an optional
COW overlay.

Usage:
- Set storage type, detect image file:
    run-qemu.py --storage ufs
    run-qemu.py --storage sdcard

- Set image file, detect storage type:
    run-qemu.py --image /path/to/disk-ufs.img

- Disable COW overlay (write to disk image):
    run-qemu.py --no-cow
"""

import argparse
import os
import sys
import shutil
import subprocess
import tempfile
import platform
import shlex
from typing import Optional

DEFAULT_UFS_IMAGE = "disk-ufs.img"
DEFAULT_SDCARD_IMAGE = "disk-sdcard.img"


def find_bios_path() -> Optional[str]:
    """
    Get OS specific aarch64 UEFI firmware path
    """
    system = platform.system()
    candidates = []

    if system == "Linux":
        # provided by qemu-efi-aarch64 in Debian bookwork/trixie/forky and in
        # Ubuntu jammy/noble/questing (as of writing)
        candidates.append("/usr/share/qemu-efi-aarch64/QEMU_EFI.fd")
    elif system == "Darwin":
        # check if brew is installed and get the prefix of the qemu recipe if
        # that recipe is installed
        brew = shutil.which("brew")
        if brew:
            try:
                completed = subprocess.run(
                    [brew, "--prefix", "qemu"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                prefix = completed.stdout.strip()
                if prefix:
                    # provided by qemu Homebrew recipe as of 10.1.1
                    candidates.append(
                        os.path.join(prefix, "share/qemu/edk2-aarch64-code.fd")
                    )
            except Exception:
                pass
    else:
        sys.stderr.write(f"Unknown system {system}, patches welcome!\n")
        sys.exit(2)

    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Run Debian disk images of various sector sizes with QEMU and an "
            "optional COW overlay."
        )
    )
    parser.add_argument(
        "--image",
        help=(
            "Path to the base disk image (.img). Default is to auto-detect "
            "disk-ufs.img or disk-sdcard.img."
        ),
    )
    parser.add_argument(
        "--storage",
        choices=["ufs", "sdcard"],
        help=(
            "Storage type. If --image isn't provided, uses default file for "
            "the storage type."
        ),
    )
    parser.add_argument(
        "--no-cow",
        action="store_true",
        help=(
            "Disable COW overlay. Without the overlay, the disk image will "
            "be modified."
        ),
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without GUI; sets -display none and -serial mon:stdio.",
    )
    parser.add_argument(
        "--qemu-args",
        dest="qemu_args",
        help=(
            "Extra arguments to pass to QEMU, e.g. "
            "'--qemu-args \"-smp 4 -m 4096\"'."
        ),
    )
    args = parser.parse_args()

    # OS; "Linux" on Debian/Ubuntu, and "Darwin" on macOS; used to detect
    # defaults
    system = platform.system()

    bios_path = find_bios_path()

    if not (
        shutil.which("qemu-system-aarch64")
        and shutil.which("qemu-img")
        and bios_path
    ):
        sys.stderr.write("Missing qemu components.\n")
        system = platform.system()
        if system == "Darwin":
            sys.stderr.write(
                "With Homebrew, install via:\n"
                "  brew install qemu\n"
            )
        elif system == "Linux":
            sys.stderr.write(
                "On Linux systems with apt, install via:\n"
                "  apt install qemu-efi-aarch64 qemu-system-arm qemu-utils\n"
            )
        else:
            sys.stderr.write(f"Unknown system {system}, patches welcome!\n")
        sys.exit(1)

    # determine image path and sector size
    if args.image:
        image_path = args.image
        if not os.path.exists(image_path):
            sys.stderr.write(f"Image not found: {image_path}\n")
            sys.exit(2)
        # if storage type was set, use it to set sector size; otherwise infer
        # from filename
        if args.storage == "ufs":
            sector_size = 4096
        elif args.storage == "sdcard":
            sector_size = 512
        else:
            # infer from filename
            fname = os.path.basename(image_path).lower()
            if "-ufs" in fname:
                sector_size = 4096
            elif "-sdcard" in fname or "-emmc" in fname:
                sector_size = 512
            else:
                # default to 4K unless specified
                sector_size = 4096
    else:
        if args.storage == "ufs":
            if not os.path.exists(DEFAULT_UFS_IMAGE):
                sys.stderr.write(
                    f"Requested storage 'ufs' but {DEFAULT_UFS_IMAGE} not "
                    "found. Please provide --image path.\n"
                )
                sys.exit(2)
            image_path = DEFAULT_UFS_IMAGE
            sector_size = 4096
        elif args.storage == "sdcard":
            if not os.path.exists(DEFAULT_SDCARD_IMAGE):
                sys.stderr.write(
                    f"Requested storage 'sdcard' but {DEFAULT_SDCARD_IMAGE} "
                    "not found. Please provide --image path.\n"
                )
                sys.exit(2)
            image_path = DEFAULT_SDCARD_IMAGE
            sector_size = 512
        else:
            # storage type not set, look for default file names
            if os.path.exists(DEFAULT_UFS_IMAGE):
                image_path = DEFAULT_UFS_IMAGE
                sector_size = 4096
            elif os.path.exists(DEFAULT_SDCARD_IMAGE):
                image_path = DEFAULT_SDCARD_IMAGE
                sector_size = 512
            else:
                sys.stderr.write(
                    f"Neither {DEFAULT_UFS_IMAGE} nor {DEFAULT_SDCARD_IMAGE} "
                    "found. Please provide --image path.\n"
                )
                sys.exit(2)

    # default to Gtk+ GUI, except on macOS where Cocoa is preferred
    display_backend = "gtk"
    if system == "Darwin":
        display_backend = "cocoa"
    if args.headless:
        display_backend = "none"

    # default to using the image as drive
    drive_file = image_path
    drive_format = "raw"

    # create and use COW overlay unless disabled
    temp_dir = None
    overlay_path = None
    if not args.no_cow:
        temp_dir = tempfile.TemporaryDirectory(prefix="qemu-cow-")
        overlay_path = os.path.join(temp_dir.name, "overlay.qcow")
        try:
            cmd = [
                "qemu-img",
                "create",
                "-b",
                os.path.abspath(image_path),
                "-f",
                "qcow2",
                "-F",
                "raw",
                overlay_path,
            ]
            print("Running:", " ".join(cmd))
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            sys.stderr.write(f"Failed to create COW overlay: {e}\n")
            if temp_dir:
                temp_dir.cleanup()
            sys.exit(1)
        drive_file = overlay_path
        drive_format = "qcow2"

    # run QEMU
    cmd = [
        "qemu-system-aarch64",
        # oldest supported CPU
        "-cpu",
        "cortex-a57",
        # smallest memory size in all supported platforms
        "-m",
        "2048",
        # performant and complete model
        "-M",
        "virt",
        "-device",
        "virtio-gpu-pci",
        "-display",
        display_backend,
        "-device",
        "usb-ehci,id=ehci",
        "-device",
        "usb-kbd",
        "-device",
        "usb-mouse",
        "-device",
        "virtio-scsi-pci,id=scsi1",
        "-device",
        f"scsi-hd,bus=scsi1.0,drive=disk1,physical_block_size={sector_size},"
        f"logical_block_size={sector_size}",
        "-drive",
        f"if=none,file={drive_file},format={drive_format},id=disk1",
        "-bios",
        bios_path,
    ]

    if args.headless:
        cmd.extend(["-serial", "mon:stdio"])

    if args.qemu_args:
        cmd.extend(shlex.split(args.qemu_args))

    print("Running:", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(f"QEMU exited with error: {e}\n")
        # fall through to cleanup
        sys.exit(e.returncode if hasattr(e, "returncode") else 1)
    finally:
        if temp_dir:
            temp_dir.cleanup()


if __name__ == "__main__":
    main()
