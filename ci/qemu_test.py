"""Tests that are entirely qemu based, so do not require test hardware

Booting the emulated guest costs minutes, so the whole module shares a single
VM: the fixture below is session scoped and logs in once. The tests therefore
run sequentially against one console and must leave it at a shell prompt.
"""

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

import os
import signal
import subprocess
import sys
import tempfile

import pexpect
import pytest

# Shell prompt of the "debian" user once logged in; every command sent to the
# console is expected to end back at it
PROMPT = "debian@debian:~$"

# Password set by login(); the image ships with "debian" and forces a reset on
# the first login, which login() walks through
PASSWORD = "new password"


@pytest.fixture(scope="session")
def vm():
    """A pexpect.spawn object attached to the serial console of a VM booted
    with a CoW base of disk-ufs.img, logged in and sitting at a shell prompt

    One VM is shared by every test in this module: booting an emulated aarch64
    guest takes minutes. The disk is a throwaway CoW overlay, so a test may
    write scratch files to the guest, but the console is shared state and each
    test has to leave it at a shell prompt. Note that logging in is part of
    setting the VM up, so the mandatory password reset flow login() walks
    through is exercised once here rather than by a test of its own; if the
    image stops requiring it, every test in this module errors.
    https://github.com/qualcomm-linux/qcom-deb-images/issues/69
    """
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
                f"if=none,file={qcow_path},format=qcow,id=disk1,cache=unsafe",
                "-device",
                "virtio-scsi-pci,id=scsi1",
                "-device",
                "scsi-hd,bus=scsi1.0,drive=disk1,physical_block_size=4096,logical_block_size=4096",
                "-nographic",
                "-bios",
                "/usr/share/AAVMF/AAVMF_CODE.fd",
            ],
            # Emulated aarch64 (no KVM) on a loaded CI runner is slow, so give
            # every expect() a generous default. The initial boot still
            # overrides this with a longer per-call timeout below.
            timeout=120,
        )
        child.logfile = sys.stdout.buffer

        login(child)

        yield child

        # No need to be nice; that would take time
        child.kill(signal.SIGKILL)

        # If this blocks then we have a problem. Better to hang than build up
        # excess qemu processes that won't die.
        child.wait()


def login(vm):
    """Wait for the login prompt of a freshly booted VM, log in as "debian",
    walk through the mandatory password reset and return at a shell prompt"""
    # This takes a minute or two on a ThinkPad T14s Gen 6 Snapdragon
    vm.expect_exact("debian login:", timeout=420)

    vm.send("debian\r\n")
    vm.expect_exact("Password:")
    vm.send("debian\r\n")
    vm.expect_exact("You are required to change your password immediately")
    vm.expect_exact("Current password:")
    vm.send("debian\r\n")
    vm.expect_exact("New password:")
    vm.send(f"{PASSWORD}\r\n")
    vm.expect_exact("Retype new password:")
    vm.send(f"{PASSWORD}\r\n")
    vm.expect_exact(PROMPT)


def test_boot_efi_not_world_accessible(vm):
    """The /boot/efi/loader/random-seed file is not readable to users"""
    # https://github.com/qualcomm-linux/qcom-deb-images/issues/279
    vm.send("journalctl | grep 'is world accessible, which is a security hole' || echo not found\r\n")
    # Need to match twice because of the serial echo of the command above
    vm.expect_exact("not found")
    vm.expect_exact("not found")
