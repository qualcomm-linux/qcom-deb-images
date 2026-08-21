"""QEMU tests for images built from an APT snapshot

These extend the generic tests in qemu_test.py, whose VM fixture and console
helpers they reuse; they only hold for an image built with the "snapshot"
debos option, so they are not part of that suite. The name of this module
deliberately matches neither of pytest's default patterns (test_*.py and
*_test.py) so that a plain "py.test-3" run does not collect it. Run it by
naming it explicitly, from the directory holding the image under test:

    py.test-3 ci/qemu_snapshot_tests.py

That is what the "Build snapshot" workflow does, by passing this path to
debos.yml's extra_qemu_tests input. See docs/snapshot.md for the option
itself.
"""

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

from qemu_test import login, run

# load qemu_test as a plugin, which is what makes its vm fixture available to
# the tests below; this does not collect its tests, they are run separately.
# importing the fixture by name would work too, but flake8 then reads every
# "vm" argument below as redefining the import (F811)
pytest_plugins = ["qemu_test"]


def test_snapshot_recorded_in_buildinfo(vm):
    """An image built from a snapshot records the snapshot it was built from"""
    login(vm)

    # the rootfs recipe writes SNAPSHOT=<timestamp> to /etc/buildinfo when, and
    # only when, the snapshot option is in use, so a missing or empty value
    # means the build silently ignored it and used the live archives
    assert run(vm, "grep -E '^SNAPSHOT=.+$' /etc/buildinfo") == 0, \
        "/etc/buildinfo has no SNAPSHOT=<timestamp>: the image was not " \
        "built from a snapshot"


def test_apt_sources_restored(vm):
    """A snapshot image boots with its APT sources back on the live mirrors"""
    login(vm)

    # the image recipe restores the live mirrors and removes the snapshot
    # helpers on the way out; leaving them behind would pin every apt update
    # on the device to a dated archive
    assert run(vm, "ls /etc/apt/sources.list.d/snapshot_*.sources") != 0, \
        "snapshot APT sources were left in the image"
    assert run(vm, "test -e /usr/local/bin/apt-snapshot-toggle") != 0, \
        "the apt-snapshot-toggle helper was left in the image"

    # apt-snapshot-toggle disables the live sources while the build runs; they
    # must all be back to "Enabled: yes" (or have no Enabled: field at all)
    assert run(vm, "grep -r '^Enabled: *no' /etc/apt/sources.list.d/") != 0, \
        "APT sources are still disabled in the image"
