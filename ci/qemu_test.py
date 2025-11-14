"""Tests that are entirely qemu based, so do not require test hardware"""

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

import os
import signal
import subprocess
import sys
import tempfile
import types

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
        spawn = pexpect.spawn(
            "qemu-system-aarch64",
            [
                "-cpu",
                "cortex-a57",
                "-m",
                "2048",
                "-M",
                "virt",
                "-drive",
                f"if=none,file={qcow_path},format=qcow,id=disk1,cache=unsafe",
                "-device",
                "virtio-scsi-pci,id=scsi1",
                "-device",
                "scsi-hd,bus=scsi1.0,drive=disk1,physical_block_size=4096,logical_block_size=4096",
                "-nographic",
                "-bios",
                "/usr/share/AAVMF/AAVMF_CODE.fd",
            ],
        )
        spawn.logfile = sys.stdout.buffer
        yield types.SimpleNamespace(spawn=spawn)

        # No need to be nice; that would take time
        spawn.kill(signal.SIGKILL)

        # If this blocks then we have a problem. Better to hang than build up
        # excess qemu processes that won't die.
        spawn.wait()


def test_password_reset_required(vm):
    """On first login, there should be a mandatory reset password flow"""
    # https://github.com/qualcomm-linux/qcom-deb-images/issues/69

    # This takes a minute or two on a ThinkPad T14s Gen 6 Snapdragon
    vm.spawn.expect_exact("debian login:", timeout=240)

    vm.spawn.send("debian\r\n")
    vm.spawn.expect_exact("Password:")
    vm.spawn.send("debian\r\n")
    vm.spawn.expect_exact("You are required to change your password immediately")
    vm.spawn.expect_exact("Current password:")
    vm.spawn.send("debian\r\n")
    vm.spawn.expect_exact("New password:")
    vm.spawn.send("new password\r\n")
    vm.spawn.expect_exact("Retype new password:")
    vm.spawn.send("new password\r\n")
    vm.spawn.expect_exact("debian@debian:~$")
