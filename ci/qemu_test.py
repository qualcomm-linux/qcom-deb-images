"""Tests that are entirely qemu based, so do not require test hardware

These run for every image debos.yml builds, so they must hold for all of them.
What differs between images is described to the tests by the environment rather
than by which tests are run: QEMU_TEST_SNAPSHOT below says whether the image
under test was built from an APT snapshot, and the snapshot expectations are
checked either way -- an image built without the option must not carry any
trace of a snapshot. Run them from the directory holding the image under test:

    py.test-3 --verbose --capture=no --ignore=rootfs
    QEMU_TEST_SNAPSHOT=20260115T000000Z py.test-3 --verbose --capture=no

Booting the emulated guest costs minutes, so the whole module shares a single
VM: the fixture below is session scoped and logs in once. The tests therefore
run sequentially against one console and must leave it at a shell prompt --
run() does, and nothing here should send anything that doesn't.
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

# Timestamp the image under test was pinned to, as passed to debos with
# -t snapshot:<timestamp>. The "Build snapshot" workflow sets this, so that the
# image is checked against the snapshot it was actually asked to build from;
# every other build leaves it unset, which asserts the opposite -- that the
# image records no snapshot at all. So pass it whenever the image under test
# was built from a snapshot, or test_snapshot_recorded_in_buildinfo() fails.
# See docs/snapshot.md.
SNAPSHOT = os.environ.get("QEMU_TEST_SNAPSHOT", "")


@pytest.fixture(scope="session")
def vm():
    """A pexpect.spawn object attached to the serial console of a VM booted
    with a CoW base of disk-ufs.img, logged in and sitting at a shell prompt

    One VM is shared by every test in this module: booting an emulated aarch64
    guest takes minutes, and none of these tests write to the guest. Note that
    logging in is part of setting it up, so the mandatory password reset flow
    login() walks through is exercised once here rather than by a test of its
    own; if the image stops requiring it, every test in this module errors.
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


def test_boot_efi_not_world_accessible(vm):
    """The /boot/efi/loader/random-seed file is not readable to users"""
    # https://github.com/qualcomm-linux/qcom-deb-images/issues/279
    assert run(vm, "journalctl | grep -q "
                   "'is world accessible, which is a security hole'") != 0, \
        "systemd-boot reports /boot/efi/loader/random-seed as world accessible"


def test_snapshot_recorded_in_buildinfo(vm):
    """The image records the snapshot it was built from, and only that one"""
    # the rootfs recipe always writes this file, and adds SNAPSHOT=<timestamp>
    # to it when, and only when, the snapshot option is in use
    assert run(vm, "test -e /etc/buildinfo") == 0, "/etc/buildinfo is missing"

    if not SNAPSHOT:
        # nothing pinned this build, so a recorded snapshot means the image is
        # not the one this run built, or that a stale timestamp leaked into the
        # recipes
        assert run(vm, "grep -q '^SNAPSHOT=' /etc/buildinfo") != 0, \
            "/etc/buildinfo records a SNAPSHOT= but the image was not built " \
            "from one; pass QEMU_TEST_SNAPSHOT if it was"
        return

    # -F -x: the recorded timestamp has to be exactly the one the build was
    # pinned to. an absent or empty value means the build silently ignored the
    # option and used the live archives; a different one means the timestamp
    # was mangled on its way to the recipes
    assert run(vm, f"grep -Fxq 'SNAPSHOT={SNAPSHOT}' /etc/buildinfo") == 0, \
        f"/etc/buildinfo does not record SNAPSHOT={SNAPSHOT}"


def test_apt_sources_are_live_mirrors(vm):
    """The image boots with its APT sources on the live mirrors"""
    # a snapshot build restores the live mirrors and removes the snapshot
    # helpers on the way out; leaving them behind would pin every apt update on
    # the device to a dated archive. an image built without the option has
    # never had them, so this holds for every image either way
    assert run(vm, "ls /etc/apt/sources.list.d/snapshot_*.sources") != 0, \
        "snapshot APT sources were left in the image"
    assert run(vm, "test -e /usr/local/bin/apt-snapshot-toggle") != 0, \
        "the apt-snapshot-toggle helper was left in the image"

    # apt-snapshot-toggle disables the live sources while the build runs; they
    # must all be back to "Enabled: yes" (or have no Enabled: field at all)
    assert run(vm, "grep -r '^Enabled: *no' /etc/apt/sources.list.d/") != 0, \
        "APT sources are still disabled in the image"
