#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause
#
# Summarise the LAVA test jobs of a workflow run as a markdown list, one
# bullet per job, linking to the job in LAVA and flagging the ones which
# did not pass:
#
#   * [Job 375279 on qcs8300-ride](https://.../375279)
#   * [Job 375280 on qrb2210-rb1](https://.../375280) (4 failed)
#   * [Job 375282 on monaco-evk](https://.../375282) (no results)
#
# It reads the artifacts foundriesio/lava-action uploads for every job of
# .github/workflows/lava-test.yml, as downloaded by the publish jobs:
#
#   <artifacts>/<suite>-<attempt>-<board>-<test>-test-job-<id>/*.json
#   <artifacts>/test-results-<suite>-<board>-<test>/*.xml
#
# Usage:  lava-job-summary.py [<artifacts directory>]

import glob
import json
import os
import re
import sys
import defusedxml.ElementTree as ET

# job details artifact, e.g. trixie-1-qrb2210-rb1-boot-test-job-375396
DETAILS_DIR = re.compile(r"^[^-]+-\d+-(?P<slug>.+)-test-job-\d+$")


def failed_tests(artifacts, slug):
    """Count the failed tests in the results of one test job.

    Return None when the job uploaded no results at all, e.g. because it
    never ran. The job details cannot answer this: they are saved right
    after the job is submitted, while it is still in state "Submitted"
    with health "Unknown".
    """
    results = glob.glob(f"{artifacts}/test-results-*-{slug}/*.xml")
    if not results:
        return None
    failed = 0
    for result in results:
        suites = ET.parse(result).getroot()
        failed += int(suites.get("failures", 0))
        failed += int(suites.get("errors", 0))
    return failed


artifacts_dir = sys.argv[1] if len(sys.argv) > 1 else "artifacts"

jobs = []
for details in glob.glob(f"{artifacts_dir}/*-test-job-*/*.json"):
    with open(details, encoding="utf-8") as details_file:
        job = json.load(details_file)

    # fall back to the LAVA device type for an unexpected artifact name;
    # it is not used otherwise as it is not always the board name, e.g.
    # ci/lava/qcs8300-ride submits to device type monaco-ride
    board = job["requested_device_type"]
    failed = None
    match = DETAILS_DIR.match(os.path.basename(os.path.dirname(details)))
    if match:
        # <board>-<test>, e.g. qcs8300-ride-boot: name the job after the
        # board directory in ci/lava/, like the submit jobs are
        board = match["slug"].rsplit("-", 1)[0]
        failed = failed_tests(artifacts_dir, match["slug"])

    if failed is None:
        status = " (no results)"
    elif failed:
        status = f" ({failed} failed)"
    else:
        status = ""
    jobs.append((board, f" * [Job {job['id']} on {board}]"
                        f"({job['url']}){status}"))

for _, line in sorted(jobs):
    print(line)
