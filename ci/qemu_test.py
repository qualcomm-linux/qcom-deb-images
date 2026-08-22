"""Tests that are entirely qemu based, so do not require test hardware

These run for every image debos.yml builds, so they must hold for all of them.
What differs between images is described to the tests by the environment rather
than by which tests are run: EXPECTED_SNAPSHOT below says whether the image
under test was built from an APT snapshot, and the snapshot expectations are
checked either way -- an image built without the option must not carry any
trace of a snapshot. Run them from the directory holding the image under test:

    py.test-3 --verbose --capture=no --ignore=rootfs
    EXPECTED_SNAPSHOT=20260115T000000Z py.test-3 --verbose --capture=no

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
# was built from a snapshot, or test_snapshot() fails.
# See docs/snapshot.md.
EXPECTED_SNAPSHOT = os.environ.get("EXPECTED_SNAPSHOT", "")


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
    #
    # Dumped to a file and grepped separately rather than piped: a pipeline
    # reports only the status of its last command, so "journalctl | grep -q"
    # cannot tell "the journal has no such line" from "the journal could not be
    # read at all" -- grep sees empty input either way and reports no match,
    # quietly passing the test. Splitting the two checks each of them.
    #
    # The dump goes through sudo because the check must not depend on the
    # "debian" user's journal access, and the file lands in the guest's /tmp,
    # which is thrown away with the CoW overlay when the VM dies.
    assert run(vm, "sudo journalctl --no-pager >/tmp/journal.txt") == 0, \
        "could not read the guest's journal"

    # == 1, not != 0: 1 is "grep read the file and found no such line", while
    # 2 would mean grep failed and the check never happened
    warning = "is world accessible, which is a security hole"
    assert run(vm, f"grep -q '{warning}' /tmp/journal.txt") == 1, \
        "systemd-boot reports /boot/efi/loader/random-seed as world accessible"


def test_snapshot(vm):
    """The image records the snapshot it was built from -- and only that one,
    or none at all -- and boots with its APT sources on the live mirrors"""
    # the rootfs recipe always writes this file, and adds SNAPSHOT=<timestamp>
    # to it when, and only when, the snapshot option is in use
    assert run(vm, "test -e /etc/buildinfo") == 0, "/etc/buildinfo is missing"

    if EXPECTED_SNAPSHOT:
        # -F -x: the recorded timestamp has to be exactly the one the build was
        # pinned to. an absent or empty value means the build silently ignored
        # the option and used the live archives; a different one means the
        # timestamp was mangled on its way to the recipes
        grep = f"grep -Fxq 'SNAPSHOT={EXPECTED_SNAPSHOT}' /etc/buildinfo"
        assert run(vm, grep) == 0, \
            f"/etc/buildinfo does not record SNAPSHOT={EXPECTED_SNAPSHOT}"
    else:
        # nothing pinned this build, so a recorded snapshot means the image is
        # not the one this run built, or that a stale timestamp leaked into the
        # recipes. == 1 rather than != 0 for the reason given further down: 1
        # is "no such line", anything else is grep failing to look
        assert run(vm, "grep -q '^SNAPSHOT=' /etc/buildinfo") == 1, \
            "/etc/buildinfo records a SNAPSHOT= but the image was not built " \
            "from one; pass EXPECTED_SNAPSHOT if it was"

    # Either way the shipped image has to point at the live mirrors: leaving it
    # pinned would tie every apt update on the device to a dated archive. A
    # snapshot build restores the mirrors and removes the snapshot helpers on
    # the way out; an image built without the option never had them.
    #
    # Every check below is a negative one, so it has to tell "looked and found
    # nothing" apart from "could not look": a missing path or an unreadable
    # file fails these commands too, and a plain "!= 0" would then pass the
    # test for entirely the wrong reason. Assert the exact status instead --
    # grep exits 1 for no match and 2 for any error, ls exits 2 for a path that
    # does not exist, test exits 1 for a file that does not exist. The greps
    # run under sudo (passwordless for this user, see the rootfs recipe) so
    # that root-only files such as /etc/apt/auth.conf.d/* cannot be the thing
    # that made grep exit non-zero.

    # the image recipe deletes both of these on its way out of a snapshot
    # build; the rootfs recipe does not, so a rootfs built with -t snapshot:
    # and an image built without it still carries them
    assert run(vm, "ls /etc/apt/sources.list.d/snapshot_*.sources") == 2, \
        "snapshot APT sources were left in the image"
    assert run(vm, "test -e /usr/local/bin/apt-snapshot-toggle") == 1, \
        "the apt-snapshot-toggle helper was left in the image"

    # the two checks above detect a snapshot by *file name*, which is only
    # equivalent to checking the URLs because the rootfs recipe derives
    # separate snapshot_*.sources files. Rewriting the live sources in place
    # instead would leave both of them green and still ship a pinned device --
    # exactly the regression this test exists to catch -- so grep for the
    # snapshot URLs themselves as well.
    #
    # over all of /etc/apt rather than just sources.list.d: mmdebstrap records
    # the mirror it bootstrapped from, i.e. the snapshot URL, in the target's
    # /etc/apt/sources.list, which is why the rootfs recipe removes that file.
    # Scanning the whole directory catches that removal regressing too.
    debian_snapshot = r"sudo grep -rq 'snapshot\.debian\.org' /etc/apt/"
    assert run(vm, debian_snapshot) == 1, \
        "APT sources still point at snapshot.debian.org"

    # the Qualcomm Linux archive is pinned by appending /<timestamp> to its
    # URL rather than by moving to a snapshot host, so match the dated path
    # instead. Anchored on the Debusine host rather than on /qualcomm/qli, so
    # that a second archive on it -- qli-staging, or whatever comes after -- is
    # covered without having to be listed here: an archive name is exactly the
    # kind of detail that would otherwise turn this into a no-op the day it
    # changes, silently and while still passing.
    #
    # Images built with -t qliaptrepo:false (Vanilla Debian) or for unstable
    # ship no Qualcomm Linux source at all, and then this passes trivially.
    # That is correct rather than merely convenient: an image with no such
    # source has no such URL that could have been left pinned.
    qli_snapshot = (r"sudo grep -rEq "
                    r"'debusine\.qualcomm\.com/.*/[0-9]{8}T[0-9]{6}Z' "
                    r"/etc/apt/")
    assert run(vm, qli_snapshot) == 1, \
        "APT sources still point at a dated Qualcomm Linux archive"

    # apt-snapshot-toggle disables the live sources while the build runs and
    # re-enables them on the way out, and only then are the snapshot files
    # deleted; nothing may be left disabled. This catches those two steps being
    # swapped, which would ship an image whose every source is "Enabled: no"
    # and whose apt update therefore finds nothing at all.
    #
    # deb822 spells false as no/false/0, hence the alternation, even though the
    # toggle itself only ever writes yes/no. Anchored at both ends so that a
    # value merely starting with one of them ("nonsense") is not a match
    disabled = (r"sudo grep -rEq '^Enabled: *(no|false|0)[[:space:]]*$' "
                r"/etc/apt/")
    assert run(vm, disabled) == 1, \
        "APT sources are still disabled in the image"
