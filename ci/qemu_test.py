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

# Since the first test checks for the mandatory password reset functionality
# that also prepares the VM for shell-based access, we make the additional
# optimisation that the fixture for a logged in VM re-uses that VM, so the
# ordering of [plain VM fixture, password reset test, logged-in VM fixture]
# matters here.


@pytest.fixture(scope="module")
def vm():
    """A pexpect.spawn object attached to the serial console of a VM freshly
    booting with a CoW base of disk-ufs.img"""
    # Since qemu booting is slow and we want fast developer iteration, we make the
    # optimisation compromise that we will not reset the qemu test fixture from a
    # fresh image for every test. Most tests should not collide with each other. If
    # we think a new test will do that and we want to make the compromise of giving
    # it an isolated environment for a slower test suite, we can deal with that
    # then.
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
                "-fsdev",
                f"local,id=fsdev0,path={os.getcwd()},security_model=none",
                "-device",
                "virtio-9p-pci,fsdev=fsdev0,mount_tag=qcom-deb-images",
            ],
        )
        spawn.logfile = sys.stdout.buffer
        yield types.SimpleNamespace(spawn=spawn, logged_in=False)

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

    vm.logged_in = True


@pytest.fixture(scope="module")
def logged_in_vm(vm):
    if not vm.logged_in:
        pytest.skip("Password reset test did not run or failed")
    return vm


def test_using_guest_tests(logged_in_vm):
    """Run the tests in qemu_guest_test.py inside the qemu guest"""
    # Statement of test success and failure that are unlikely to appear by
    # accident
    SUCCESS_NOTICE = "All ci/qemu_guest_test.py tests passed"
    FAILURE_NOTICE = "Some ci/qemu_guest_test.py tests failed"
    # We use apt-get -U here and the apt_dependencies fixture in
    # qemu_guest_test.py relies on this.
    SCRIPT = f"""sudo -i sh <<EOT
apt-get install -Uy --no-install-recommends python3-pytest
mkdir qcom-deb-images
mount -t 9p qcom-deb-images qcom-deb-images
cd qcom-deb-images
py.test-3 -vvm guest ci/qemu_guest_test.py && echo "{SUCCESS_NOTICE}" || echo "{FAILURE_NOTICE}"
EOT
"""
    logged_in_vm.spawn.send(SCRIPT.replace("\r", "\r\n"))

    # Match a known string for when pytest starts. Otherwise we catch the echo
    # of our own printing of SUCCESS_NOTICE and FAILURE_NOTICE that appears
    # before, causing us to falsely believe that it was done. The timeout is
    # required to give enough time for the installation of python3-pytest to
    # finish.
    logged_in_vm.spawn.expect_exact("test session starts", timeout=120)
    match = logged_in_vm.spawn.expect_exact(
        [SUCCESS_NOTICE, FAILURE_NOTICE], timeout=120
    )
    assert match == 0, "ci/qemu_guest_test.py tests failed"
