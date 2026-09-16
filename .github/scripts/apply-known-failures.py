#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

"""Apply a known failures list to the LAVA JUnit XML result files.

The LAVA JUnit files are published as a check by
publish-unit-test-result-action, which fails the workflow on any <failure>.
Without this script a failure that is already known and accepted would keep
that check red forever.

Known failures are carried per debian suite in
.github/known-failures/<suite>.yaml, see the README in that directory. This
script rewrites the result files in place so that:

  * a failing test that is on the list becomes <skipped> ("known failure"), so
    it no longer fails the check but stays visible in the report,
  * a passing test that is on the list becomes <failure> ("unexpected pass"),
    which is the signal that the entry has to be removed from the list.

The device a result file belongs to is taken from its name, which the
lava-test-plans action builds as "<prefix>-<device>-<job file>" and lava-action
saves with an .xml suffix.

With --validate the script does not touch any result file. It only checks the
lists themselves: their syntax, and that every device and kernel they name is
one that is really tested, as an entry naming something untested would silently
never be applied. Add --syntax-only to check the syntax alone, which is all
that can be asked of lists that do not come from this branch.
"""

import argparse
import os
import pathlib
import sys
import xml.etree.ElementTree as ET

import defusedxml.ElementTree as DET
import yaml

# suite holding the LAVA infrastructure steps rather than the actual tests
LAVA_SUITE = "lava"

# table of the boards tested per kernel flavour
BOARD_TABLE = ".github/lava-boards.yml"

# device standing for every device tested
ANY_DEVICE = "*"


def annotation(path, message):
    """Format a message as a GitHub annotation when running in a workflow."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return f"::error file={path}::{message}"
    return f"{path}: {message}"


def fail(path, message):
    sys.exit(annotation(path, message))


def load_entry(path, device, entry):
    """Return (test name, comment, kernels) for one entry of a list.

    An entry is either a bare test name or a mapping with a "test", an optional
    "comment" naming the issue the failure is tracked in, and an optional
    "kernels" list restricting it to some kernel flavours.
    """
    if isinstance(entry, str):
        return entry, "", None
    if not isinstance(entry, dict):
        fail(path,
             f"{device}: expected a test name or a mapping, got {entry!r}")
    unknown = set(entry) - {"test", "comment", "kernels"}
    if unknown:
        fail(path, f"{device}: unknown key(s) {', '.join(sorted(unknown))}")
    test = entry.get("test")
    if not isinstance(test, str) or not test:
        fail(path, f"{device}: entry {entry!r} is missing a test name")
    kernels = entry.get("kernels")
    if kernels is not None:
        if not isinstance(kernels, list) or not all(
                isinstance(k, str) for k in kernels):
            fail(path, f"{device}: {test}: kernels has to be a list of names")
    return test, str(entry.get("comment", "")), kernels


def load_list(path, kernel=None):
    """Return {device: {test name: comment}} for one known failures list.

    Entries restricted to other kernel flavours are dropped when a kernel is
    given, and kept when it is not, which is what validation wants.
    """
    try:
        content = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as error:
        fail(path, f"not valid YAML: {error}")
    if not isinstance(content, dict):
        fail(path, "expected a mapping of device to list of tests")
    devices = {}
    for device, entries in content.items():
        if not isinstance(entries, list):
            fail(path, f"{device}: expected a list of test names")
        tests = {}
        for entry in entries:
            test, comment, kernels = load_entry(path, device, entry)
            if test in tests:
                fail(path, f"{device}: {test} is listed more than once")
            if (kernel is not None and kernels is not None
                    and kernel not in kernels):
                continue
            tests[test] = comment
        devices[str(device)] = tests
    return devices


def known_tests_for(name, devices):
    """Return the {test name: comment} that apply to one result file.

    The prefix, the device and the job file all contain dashes, so the device
    is found by looking for the names that are listed rather than by splitting
    the file name up. The longest match wins, so a board does not shadow
    another whose name it is a prefix of.
    """
    tests = dict(devices.get(ANY_DEVICE, {}))
    for device in sorted(devices, key=len, reverse=True):
        if device == ANY_DEVICE:
            continue
        if f"-{device}-" in name:
            # a device specific entry overrides the one listed for all devices
            tests.update(devices[device])
            break
    return tests


def annotate(message, comment):
    """Append the comment of a known failures entry to a JUnit message."""
    return f"{message} ({comment})" if comment else message


def apply_to_testcase(testcase, suite_name, comment):
    """Rewrite one testcase. Returns a description of the change or None."""
    name = testcase.get("name")
    failure = testcase.find("failure")
    if failure is None:
        failure = testcase.find("error")
    if failure is not None:
        # known failure: report it as skipped so it does not fail the check
        original = failure.get("message", "failed")
        testcase.remove(failure)
        skipped = ET.SubElement(testcase, "skipped")
        skipped.set("type", "known failure")
        skipped.set(
            "message",
            annotate(
                "known failure: listed in .github/known-failures, "
                f"original result: {original}",
                comment,
            ),
        )
        return f"{suite_name}/{name}: fail -> known failure (skipped)"
    if testcase.find("skipped") is not None:
        # the test did not run, nothing to say about the known failure
        return None
    # the test passed although it is expected to fail
    failure = ET.SubElement(testcase, "failure")
    failure.set("type", "unexpected pass")
    failure.set(
        "message",
        annotate(
            "unexpected pass: the test is listed as a known failure in "
            ".github/known-failures, remove it from the list",
            comment,
        ),
    )
    return f"{suite_name}/{name}: pass -> unexpected pass (failure)"


def count(testsuite, tag):
    return len(testsuite.findall(f"./testcase/{tag}"))


def refresh_counters(testsuites):
    """Recompute the counters of every testsuite and of the root element."""
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for testsuite in testsuites.findall("testsuite"):
        counters = {
            "tests": len(testsuite.findall("testcase")),
            "failures": count(testsuite, "failure"),
            "errors": count(testsuite, "error"),
            "skipped": count(testsuite, "skipped"),
        }
        for key, value in counters.items():
            testsuite.set(key, str(value))
            totals[key] += value
    for key, value in totals.items():
        # the root element of the LAVA JUnit output has no "skipped" counter
        if key != "skipped" or "skipped" in testsuites.attrib:
            testsuites.set(key, str(value))


def process(path, known_tests):
    """Apply known_tests to one result file. Returns the list of changes."""
    # the result files come from LAVA, parse them defensively
    tree = DET.parse(path)
    testsuites = tree.getroot()
    changes = []
    for testsuite in testsuites.findall("testsuite"):
        suite_name = testsuite.get("name", "")
        if suite_name == LAVA_SUITE:
            continue
        for testcase in testsuite.findall("testcase"):
            name = testcase.get("name")
            if name in known_tests:
                change = apply_to_testcase(
                    testcase, suite_name, known_tests[name])
                if change:
                    changes.append(change)
    if changes:
        refresh_counters(testsuites)
        tree.write(path, encoding="utf-8", xml_declaration=True)
    return changes


def load_board_table(path):
    """Return {kernel: set(devices)} from the board table."""
    table = yaml.safe_load(pathlib.Path(path).read_text()) or {}
    kernels = {}
    for kernel, lists in table.items():
        devices = set()
        for key in ("boot", "premerge"):
            devices |= {str(d) for d in (lists or {}).get(key) or []}
        kernels[str(kernel)] = devices
    return kernels


def validate(known_failures_dir, board_table_path):
    """Check every list against the boards and kernels really tested."""
    kernels = load_board_table(board_table_path)
    if not kernels:
        sys.exit(f"{board_table_path}: no kernel found, "
                 "is it still the board table?")
    tested_devices = set().union(*kernels.values())
    print(
        f"{board_table_path} tests {len(kernels)} kernel flavour(s) on "
        f"{len(tested_devices)} board(s)"
    )

    errors = []
    for path in sorted(pathlib.Path(known_failures_dir).glob("*.yaml")):
        content = yaml.safe_load(path.read_text()) or {}
        devices = load_list(path)
        for device in sorted(set(devices) - {ANY_DEVICE} - tested_devices):
            errors.append(
                annotation(
                    path,
                    f"{device} is not tested by any kernel flavour in "
                    f"{board_table_path}, the entries listed for it would "
                    "never be applied. Tested: "
                    f"{', '.join(sorted(tested_devices))}",
                )
            )
        for device, entries in content.items():
            for entry in entries or []:
                if not isinstance(entry, dict):
                    continue
                listed_kernels = set(entry.get("kernels") or [])
                for kernel in sorted(listed_kernels - set(kernels)):
                    errors.append(
                        annotation(
                            path,
                            f"{device}: {entry.get('test')}: {kernel} is "
                            f"not a kernel flavour in {board_table_path}. "
                            "Known: "
                            f"{', '.join(sorted(kernels))}",
                        )
                    )
        for device, tests in sorted(devices.items()):
            for test, comment in sorted(tests.items()):
                if not comment:
                    print(
                        f"::warning file={path}::{device}: {test} has no "
                        "comment, add the issue it is tracked in"
                    )

    for error in errors:
        print(error)
    if errors:
        sys.exit(f"{len(errors)} error(s) in {known_failures_dir}")
    print(f"{known_failures_dir}: all lists are valid")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--known-failures-dir",
        default=".github/known-failures",
        help="directory holding the per suite known failures lists",
    )
    parser.add_argument(
        "--known-failures-file",
        help="the list to apply, usually <known failures dir>/<suite>.yaml",
    )
    parser.add_argument(
        "--kernel",
        default="",
        help="kernel flavour under test; entries naming a different "
        "flavour in their kernels list are not applied",
    )
    parser.add_argument(
        "--results-dir",
        help="directory holding the downloaded LAVA JUnit result files",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="only check the known failures lists, not any result file",
    )
    parser.add_argument(
        "--board-table",
        default=BOARD_TABLE,
        help="board table the tested devices and kernels are read from",
    )
    parser.add_argument(
        "--syntax-only",
        action="store_true",
        help="with --validate, only check that the lists are well formed, not "
        "that they match the boards and kernels that are tested",
    )
    args = parser.parse_args()

    if args.validate:
        # The syntax is a property of the lists themselves, the tested boards
        # are a property of this branch. Lists coming from somewhere else - the
        # branch or fork a pull request is built from - are only checked for
        # syntax, so that they can add or drop a board without being rejected.
        found = sorted(pathlib.Path(args.known_failures_dir).glob("*.yaml"))
        if not found:
            sys.exit("no known failures list found in "
                     f"{args.known_failures_dir}")
        for path in found:
            devices = load_list(path)
            listed = sum(len(tests) for tests in devices.values())
            print(f"{path.name}: {listed} known failure(s) listed")
        if not args.syntax_only:
            validate(args.known_failures_dir, args.board_table)
        return

    if not args.results_dir or not args.known_failures_file:
        parser.error("--results-dir and --known-failures-file are required")
    path = pathlib.Path(args.known_failures_file)
    if not path.is_file():
        print(f"{path}: no such list, no known failure to apply")
        return
    devices = load_list(path, kernel=args.kernel or None)
    listed = sum(len(tests) for tests in devices.values())
    print(f"{path.name}: {listed} known failure(s) listed "
          f"for kernel {args.kernel!r}")

    total = 0
    for result in sorted(pathlib.Path(args.results_dir).rglob("*.xml")):
        known_tests = known_tests_for(result.name, devices)
        if not known_tests:
            continue
        for change in process(result, known_tests):
            print(f"{result.name}: {change}")
            total += 1
    print(f"applied the known failures list to {total} test result(s)")


if __name__ == "__main__":
    main()
