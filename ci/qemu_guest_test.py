"""qemu-based tests that are copied into the guest and run there"""

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# These tests are run inside the qemu guest as root using its own pytest runner
# invocation.

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
