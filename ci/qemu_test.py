"""Tests that are entirely qemu based, so do not require test hardware

These run for every image debos.yml builds, so they must hold for all of them.
Tests which only make sense for one kind of image live in their own module
importing the helpers from here, and are run by naming that module explicitly;
see ci/qemu_snapshot_tests.py.
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
# the first login, see test_password_reset_required()
PASSWORD = "new password"


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


def run(vm, command):
    """Run *command* in the VM's shell and return its exit status

    The serial console echoes back everything that is sent to it, so anything
    matched right after send() matches the echo of the command rather than its
    output. Rather than matching the output, ask the shell for the exit status
    of the command afterwards: the literal "rc=$?" of the echoed line can never
    match the "rc=<digits>" the shell prints.
    """
    vm.send(f"{command}\r\n")
    vm.expect_exact(PROMPT)
    vm.send('echo "rc=$?"\r\n')
    vm.expect(r"rc=(\d+)")
    status = int(vm.match.group(1))
    vm.expect_exact(PROMPT)
    return status


def test_password_reset_required(vm):
    """On first login, there should be a mandatory reset password flow"""
    # https://github.com/qualcomm-linux/qcom-deb-images/issues/69
    login(vm)

    # The /boot/efi/loader/random-seed file should not be readable to users
    # https://github.com/qualcomm-linux/qcom-deb-images/issues/279
    vm.send("journalctl | grep 'is world accessible, which is a security hole' || echo not found\r\n")
    # Need to match twice because of the serial echo of the command above
    vm.expect_exact("not found")
    vm.expect_exact("not found")
