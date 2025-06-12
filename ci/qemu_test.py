"""Tests that are entirely qemu based, so do not require test hardware"""

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

import os
import signal
import subprocess
import tempfile

import pexpect
import pytest


@pytest.fixture
def vm():
    """A pexpect.spawn object attached to the serial console of a VM freshly
    booting with a CoW base of disk-ufs.img"""
    with tempfile.TemporaryDirectory() as tmpdir:
        qcow_path = os.path.join(tmpdir, "disk1.qcow")
        subprocess.run(
            [
                "qemu-img",
                "create",
                "-b",
                os.path.join(os.getcwd(), "disk-ufs.img"),
                "-f",
                "qcow",
                "-F",
                "raw",
                qcow_path,
            ],
            check=True,
        )
        child = pexpect.spawn(
            "qemu-system-aarch64",
            [
                "-cpu",
                "cortex-a57",
                "-m",
                "2048",
                "-M",
                "virt",
                "-drive",
                f"if=none,file={qcow_path},format=qcow,id=disk1",
                "-device",
                "virtio-scsi-pci,id=scsi1",
                "-device",
                "scsi-hd,bus=scsi1.0,drive=disk1,physical_block_size=4096,logical_block_size=4096",
                "-nographic",
                "-bios",
                "/usr/share/AAVMF/AAVMF_CODE.fd",
            ],
        )
        yield child

        # No need to be nice; that would take time
        child.kill(signal.SIGKILL)

        # If this blocks then we have a problem. Better to hang than build up
        # excess qemu processes that won't die.
        child.wait()
