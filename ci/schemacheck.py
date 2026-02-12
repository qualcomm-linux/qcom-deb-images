#!/usr/bin/env python3

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
#
# SPDX-License-Identifier: BSD-3-Clause

import os
import sys
import yaml
import voluptuous  # pylint: disable=import-error
from lava_common.schemas import validate  # pylint: disable=import-error

exitcode = 0

for root, dirs, files in os.walk(sys.argv[1]):
    for fname in files:
        if fname.endswith(".yaml"):
            filename = os.path.join(root, fname)

            try:
                f = open(filename, "rb")
                y = yaml.safe_load(f)
                f.close()
                validate(y)
                print(f"{filename} is valid")
            except voluptuous.Invalid as e1:
                print(f"{filename} is invalid")
                print(e1.msg)
                print(e1.path)
                exitcode += 1
            except yaml.error.MarkedYAMLError as e2:
                print(f"{filename} is invalid")
                print(e2.problem)
                print(e2.problem_mark)
                exitcode += 1
sys.exit(exitcode)
