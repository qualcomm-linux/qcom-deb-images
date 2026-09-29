"""Tests that are entirely qemu based, so do not require test hardware

These run for every image debos.yml builds, so they must hold for all of them.
What differs between images is described to the tests by the environment rather
than by which tests are run: EXPECTED_SUITE below says which Debian suite the
image under test was built for, and EXPECTED_SNAPSHOT whether it was built
from an APT snapshot -- the snapshot expectations are checked either way, as
an image built without the option must not carry any trace of a snapshot. Run
them from the directory holding the image under test:

    py.test-3 --verbose --capture=no --ignore=rootfs
    EXPECTED_SNAPSHOT=20260115T000000Z EXPECTED_SUITE=trixie \
        py.test-3 --verbose --capture=no

Booting the emulated guest costs minutes, so the whole module shares a single
VM: the fixture below is session scoped and logs in once. The tests therefore
run sequentially against one console and must leave it at a shell prompt --
run() does, and nothing here should send anything that doesn't.
"""

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

import os
import re
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

# Variable through which a workflow passes the whole set of EXPECTED_* below at
# once, as NAME=value lines; see image_environment()
PAYLOAD = "QEMU_TEST_ENV"

# What may appear left of the "=" in one of those lines, i.e. the shell's rule
# for a variable name
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")

# Every variable these tests read, and the whole of what a caller may say about
# the image. Kept as a whitelist so a name nothing reads is an error rather
# than a no-op: EXPECTED_SNAPSHOT and EXPECTED_SNAPSOHT are one keystroke
# apart, and the typo would not fail, it would quietly turn test_snapshot()
# from "this image was pinned to that timestamp" into "this image was never
# pinned at all" -- and pass. Add to this when adding a variable.
KNOWN = ("EXPECTED_SNAPSHOT", "EXPECTED_SUITE")


def parse_payload(payload):
    """Return the NAME=value lines of *payload* as a dict, ignoring blank ones

    An empty payload is a caller which passed no variables.

    A line which is not an assignment, or which sets a name outside KNOWN,
    raises ValueError rather than being dropped. What these variables say is
    not optional detail -- an EXPECTED_SNAPSHOT which never arrives does not
    mean "skip the snapshot check", it means "this image was built without a
    snapshot" -- so a dropped line would have the tests assert the opposite of
    what was meant, and pass.
    """
    payload = payload.strip()
    if not payload:
        return {}

    variables = {}
    for line in payload.splitlines():
        line = line.strip()
        if not line:
            continue

        name, separator, value = line.partition("=")
        if not separator or not NAME.match(name):
            raise ValueError(f"{PAYLOAD} is not NAME=value: {line!r}")
        if name not in KNOWN:
            raise ValueError(
                f"{PAYLOAD} sets {name}, which no test here reads; "
                f"expected one of: {', '.join(KNOWN)}")
        variables[name] = value
    return variables


def image_environment():
    """Return what this run has been told about the image under test

    One source, never a mix of the two. QEMU_TEST_ENV, if it is set at all,
    is the whole of it, and the ambient environment is ignored; otherwise the
    ambient environment is read directly. In CI the payload is the caller's
    complete statement about the image it just built, so a stray EXPECTED_*
    inherited by a runner has no way to contribute to it -- and by hand there
    is no payload, so the variables work exactly as any other.

    A workflow has the whole set to pass at once and no good way to say so:
    listing them in the step's env: would hardcode in debos.yml which
    variables its callers care about, and splitting a multi-line input into
    one variable per line would have to happen in the test step, which runs in
    a container with no bash -- a heredoc, the positional parameters as the
    only list POSIX sh has, and a case glob standing in for a pattern match,
    none of which could be run outside CI. So debos.yml passes its
    qemu_test_env input straight through as one payload of NAME=value lines,
    and it is split here.

    A payload in a variable rather than a file or a CSV: the transport was
    never the problem, a file would have to be written and cleaned up by the
    same shell this avoids, and CSV or JSON would buy quoting rules for values
    which are timestamps and suite names.

    Every name in KNOWN is reported, with where it was read from and whether
    it was set, because both halves change what the tests assert. An unset
    EXPECTED_SNAPSHOT is not "one less check", it is the opposite check, and a
    caller which meant to pass one and did not looks from the outside exactly
    like one which never meant to.

    That report needs --capture=no to be seen, which debos.yml passes. This
    runs while the module is imported, i.e. during collection, and pytest
    swallows anything printed then unless collection goes on to fail. A
    pytest_report_header hook would show without it, but pytest only takes
    hooks from conftest.py and plugins, never from a test module.
    """
    payload = os.environ.get(PAYLOAD)

    if payload is None:
        source = "the environment"
        variables = {name: os.environ[name]
                     for name in KNOWN if name in os.environ}
    else:
        source = PAYLOAD
        variables = parse_payload(payload)

    print(f"QEMU test environment, from {source}:")
    for name in KNOWN:
        if name in variables:
            print(f"  {name}={variables[name]}")
        else:
            print(f"  {name} is unset")

    return variables


# Evaluated at import, so the EXPECTED_* below are in place before pytest
# collects anything in this module
ENVIRONMENT = image_environment()

# Debian suite the image under test was built for, as passed to debos with
# -t suite:<suite> and as /etc/os-release spells it, e.g. "trixie". debos.yml
# sets this for every image it builds, from the suite it was asked for, so no
# caller has to. There is no meaningful assertion to make when it is unset --
# every image is some suite -- so test_suite() skips instead, which is what
# running these by hand without it does.
#
# Don't pass "sid" or "unstable": those track whichever codename is next, so
# /etc/os-release does not record either of them and the check would fail on a
# perfectly good image.
EXPECTED_SUITE = ENVIRONMENT.get("EXPECTED_SUITE", "")

# Timestamp the image under test was pinned to, as passed to debos with
# -t snapshot:<timestamp>. The "Build snapshot" workflow sets this, so that the
# image is checked against the snapshot it was actually asked to build from;
# every other build leaves it unset, which asserts the opposite -- that the
# image records no snapshot at all. So pass it whenever the image under test
# was built from a snapshot, or test_snapshot() fails.
# See docs/snapshot.md.
EXPECTED_SNAPSHOT = ENVIRONMENT.get("EXPECTED_SNAPSHOT", "")


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
    # As the "debian" user, which is in the "adm" group and so can read the
    # system journal; the dump lands in the guest's /tmp, which is thrown away
    # with the CoW overlay when the VM dies.
    assert run(vm, "journalctl --no-pager >/tmp/journal.txt") == 0, \
        "could not read the guest's journal"

    # == 1, not != 0: 1 is "grep read the file and found no such line", while
    # 2 would mean grep failed and the check never happened
    warning = "is world accessible, which is a security hole"
    assert run(vm, f"grep -q '{warning}' /tmp/journal.txt") == 1, \
        "systemd-boot reports /boot/efi/loader/random-seed as world accessible"


# skipif rather than a pytest.skip() in the body: a marker is evaluated before
# the vm fixture is requested, so running this test alone with EXPECTED_SUITE
# unset doesn't boot a VM only to skip. In CI the fixture is session scoped and
# already up, so it costs nothing there either way
@pytest.mark.skipif(not EXPECTED_SUITE,
                    reason="EXPECTED_SUITE is unset, so the suite the image "
                           "was built for is not known")
def test_suite(vm):
    """The image is the Debian suite the build was asked for"""
    # os-release rather than the APT sources: this asks what the image *is*,
    # which is what a caller asking for forky and getting trixie cares about,
    # and it does not depend on how the recipe happens to name its sources.
    # It comes from the base-files package the bootstrap pulled in, so it also
    # catches a suite which was only applied to the later package installs.
    assert run(vm, "test -e /etc/os-release") == 0, \
        "/etc/os-release is missing"

    # -F -x: the whole line, so that "trixie" cannot match a "trixie/sid"
    grep = f"grep -Fxq 'VERSION_CODENAME={EXPECTED_SUITE}' /etc/os-release"
    assert run(vm, grep) == 0, \
        "/etc/os-release does not record " \
        f"VERSION_CODENAME={EXPECTED_SUITE}; the image is not the suite " \
        "this build asked for"


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
    # does not exist, test exits 1 for a file that does not exist.
    #
    # That is also why the greps stay inside /etc/apt/sources.list.d/ rather
    # than sweeping /etc/apt: the .sources files there are world readable and
    # the directory always exists, so exit 1 really does mean "found nothing",
    # whereas a recursive grep of /etc/apt would exit 2 on root-only files such
    # as auth.conf.d/* and pass every check for the wrong reason.

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
    # exactly the regression this test exists to catch -- so grep for what a
    # pinned source actually looks like as well. The three checks below do that
    # from three angles, none of which names an individual source, so they hold
    # for every entry in sources.list.d/ rather than for the ones that happened
    # to exist when they were written.

    # by host: snapshot.debian.org serves the Debian archives, and it also
    # accepts a short YYYYMMDD timestamp, which the dated-URL check below would
    # not match
    debian_snapshot = (r"grep -rq 'snapshot\.debian\.org' "
                       r"/etc/apt/sources.list.d/")
    assert run(vm, debian_snapshot) == 1, \
        "APT sources still point at snapshot.debian.org"

    # the one snapshot URL that would not live in sources.list.d/: mmdebstrap
    # records the mirror it bootstrapped from, i.e. the snapshot URL, in the
    # target's /etc/apt/sources.list. The rootfs recipe removes that file
    # outright, so assert its absence rather than grepping it
    assert run(vm, "test -e /etc/apt/sources.list") == 1, \
        "/etc/apt/sources.list was left in the image; it still holds the " \
        "mirror mmdebstrap bootstrapped from"

    # not every archive is pinned by moving to a snapshot host: the Qualcomm
    # Linux one is pinned by appending /<timestamp> to its own URL. Rather than
    # listing the archives that do that -- qli today, qli-staging or whatever
    # comes after it tomorrow, and an archive name is exactly the kind of
    # detail that would silently turn this check into a no-op the day it
    # changes -- match the shape all of them share: a URL whose last path
    # component is a snapshot timestamp. That covers every source in the file,
    # including any added after this was written.
    #
    # Images built with -t qliaptrepo:false (Vanilla Debian) or for unstable
    # ship no Qualcomm Linux source at all, and then this simply finds nothing
    # to object to; an image with no such source has no such URL that could
    # have been left pinned.
    dated_uri = (r"grep -rEq 'https?://[^[:space:]]*/[0-9]{8}T[0-9]{6}Z' "
                 r"/etc/apt/sources.list.d/")
    assert run(vm, dated_uri) == 1, \
        "an APT source still points at a dated snapshot archive"

    # the other half of "pinned", and the one that does not depend on the URL
    # form at all: the derivation gives every snapshot source a
    # "Check-Valid-Until: no", because a static snapshot's Release goes stale.
    # No live source has any business carrying it, so it flags a source left in
    # snapshot mode even if its URL were rewritten to a shape neither of the
    # greps above knows about.
    valid_until = (r"grep -rEq '^Check-Valid-Until: *(no|false|0)"
                   r"[[:space:]]*$' /etc/apt/sources.list.d/")
    assert run(vm, valid_until) == 1, \
        "an APT source still has Check-Valid-Until disabled"

    # same relaxation, but applied globally rather than per source: mmdebstrap
    # persists the Acquire::Check-Valid-Until "false" it bootstrapped with into
    # the rootfs, in /etc/apt/apt.conf.d/99mmdebstrap. The rootfs recipe strips
    # that setting out, though it may leave the file itself behind holding the
    # other options mmdebstrap wrote, so grep for the setting rather than
    # testing for the file. Scanning the whole directory also catches the same
    # relaxation reappearing under some other name.
    global_valid_until = "grep -rq 'Check-Valid-Until' /etc/apt/apt.conf.d/"
    assert run(vm, global_valid_until) == 1, \
        "an APT config in the image sets Check-Valid-Until; the image would " \
        "accept expired Release files for every archive"

    # apt-snapshot-toggle disables the live sources while the build runs and
    # re-enables them on the way out, and only then are the snapshot files
    # deleted; nothing may be left disabled. This catches those two steps being
    # swapped, which would ship an image whose every source is "Enabled: no"
    # and whose apt update therefore finds nothing at all.
    #
    # deb822 spells false as no/false/0, hence the alternation, even though the
    # toggle itself only ever writes yes/no. Anchored at both ends so that a
    # value merely starting with one of them ("nonsense") is not a match
    disabled = (r"grep -rEq '^Enabled: *(no|false|0)[[:space:]]*$' "
                r"/etc/apt/sources.list.d/")
    assert run(vm, disabled) == 1, \
        "APT sources are still disabled in the image"
