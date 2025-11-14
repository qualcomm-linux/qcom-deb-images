"""qemu-based tests that are copied into the guest and run there"""

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# These tests are run inside the qemu guest as root using its own pytest runner
# invocation.

import subprocess
import tempfile

import pytest

# Mark this module so that the main test runner can skip it when running from
# the host. However, the guest test runner does not use this mark but instead
# explicitly calls this file. Marks require test collection, and the guest test
# runner isn't going to have dependencies installed that are only needed for
# host tests, causing guest test collection to fail otherwise.
pytestmark = pytest.mark.guest


def test_empty():
    # The empty test. This is nevertheless useful as its presence ensures that
    # the host is calling the guest test suite in this module correctly.
    pass


# To keep tests fast for developer iteration, just install all the test
# dependencies together once for all tests defined here. It is likely that our
# tests here are not going to interact. If they do, then we can decide whether
# to compromise on this at that time.
@pytest.fixture(scope="module")
def apt_dependencies():
    # To speed things up, we deliberately skip the apt-get update here on the
    # assumption that it was arranged by whatever is running the test. This is
    # arranged from qemu_test.py::test_using_guest_tests() instead.
    subprocess.run(
        ["apt-get", "install", "-y", "--no-install-recommends", "sudo", "gdb"],
        check=True,
    )


def test_sudo_no_fqdn(apt_dependencies):
    """sudo should not call FQDN lookup functions

    See: https://github.com/qualcomm-linux/qcom-deb-images/issues/193
    """
    with tempfile.NamedTemporaryFile(
        mode="w", delete_on_close=False
    ) as gdb_commands_file:
        print(
            "catch load",
            "run",
            "del 1",
            sep="\n",
            file=gdb_commands_file,
        )
        for fn_name in [
            "gethostbyaddr",
            "getnameinfo",
            "getaddrinfo",
            "gethostbyname",
        ]:
            print(
                f"break {fn_name}",
                f"commands",
                f'    print "{fn_name} called\\n"',
                f"    quit 1",
                f"end",
                sep="\n",
                file=gdb_commands_file,
            )
        print("continue", file=gdb_commands_file)
        gdb_commands_file.close()

        subprocess.run(
            [
                "gdb",
                "--batch",
                "-x",
                gdb_commands_file.name,
                "--args",
                "sudo",
                "true",
            ],
            check=True,
        )
