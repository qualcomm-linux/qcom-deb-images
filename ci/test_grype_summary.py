"""Tests for the Grype vulnerability summary"""

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

import json
import copy
import os
import subprocess
import sys

import pytest

SCRIPTS = os.path.join(os.path.dirname(__file__), os.pardir, "scripts")

PRODUCT = "pkg:generic/qualcomm-linux-debian-rootfs"


def script(name, *args, cwd=None):
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, name), *args],
        capture_output=True, text=True, check=False, cwd=cwd)


def deb(name, version, source=None, source_version=None):
    metadata = {}
    if source:
        metadata["source"] = source
    if source_version:
        metadata["sourceVersion"] = source_version
    return {
        "name": name, "version": version, "type": "deb", "metadata": metadata,
        "purl": f"pkg:deb/debian/{name}@{version}?arch=arm64&distro=debian-13",
    }


def statement(status, subs, name="CVE-2026-12345", **extra):
    return {
        "vulnerability": {"name": name},
        "products": [{"@id": PRODUCT,
                      "subcomponents": [{"@id": s} for s in subs]}],
        "status": status, **extra,
    }


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return str(path)


def grype_match(name="openssl", severity="High", version="3.5.0-1",
                vuln_id="CVE-2026-12345", fix_state="not-fixed", versions=()):
    return {
        "vulnerability": {
            "id": vuln_id, "severity": severity,
            "fix": {"state": fix_state, "versions": list(versions)},
        },
        "artifact": {**deb(name, version), "id": f"{name}-id"},
        "matchDetails": [{"type": "exact-direct-match",
                          "matcher": "dpkg-matcher"}],
    }


def vex_matched(match, stmt):
    # the match as Grype 0.118 reports it when a statement re-added it
    match = copy.deepcopy(match)
    match["matchDetails"].append({
        "type": "exact-direct-match", "matcher": "openvex-matcher",
        "searchedBy": {"Vulnerability": match["vulnerability"]["id"],
                       "Product": f"{PRODUCT}@b1",
                       "Subcomponents": [match["artifact"]["purl"]]},
        "found": {"Statement": stmt},
    })
    return match


def test_summary_layout_and_suppressed_fix_versions(tmp_path):
    fixed = grype_match(fix_state="fixed", versions=["3.5.1-1"])
    fixed["appliedIgnoreRules"] = [{"vex-status": "fixed"}]
    unaffected = grype_match("libssl3t64", fix_state="wont-fix")
    unaffected["appliedIgnoreRules"] = [{
        "vex-status": "not_affected",
        "vex-justification": "vulnerable_code_not_present",
    }]
    open_match = grype_match(vuln_id="CVE-2026-99999",
                             fix_state="wont-fix")
    data = {"matches": [open_match],
            "ignoredMatches": [fixed, unaffected, copy.deepcopy(fixed)]}
    result = script("grype-vulnerability-summary.py",
                    write(tmp_path, "grype.json", data))
    assert result.returncode == 0, result.stderr
    text = result.stdout
    header = "| Vulnerability | Severity | Packages | Fixed in | Status |"
    assert text.count(header) == 2
    assert text.index("critical and high severity") < text.index(
        "suppressed vulnerabilities")
    assert "| Suppressed | 1 |" in text
    assert "Total unique vulnerabilities: **1**" in text
    suppressed = next(line for line in text.splitlines()
                      if line.startswith("| CVE-2026-12345 |"))
    assert "`openssl 3.5.0-1`" in suppressed
    assert "`libssl3t64 3.5.0-1`" in suppressed
    # Grype's fix state and versions are kept for suppressed findings
    assert "`3.5.1-1`" in suppressed
    assert "wont-fix" in suppressed
    assert "VEX fixed; VEX not_affected" in suppressed


@pytest.mark.parametrize("severity", ["Low", "Negligible", "Unknown"])
def test_summary_only_suppressed(tmp_path, severity):
    match = grype_match(severity=severity)
    data = {"matches": [], "ignoredMatches": [{
        "match": match,
        "appliedIgnoreRules": [{"vex-status": "not_affected"}],
    }]}
    result = script("grype-vulnerability-summary.py",
                    write(tmp_path, "grype.json", data))
    assert result.returncode == 0, result.stderr
    assert "| Suppressed | 1 |" in result.stdout
    assert "| CVE-2026-12345 |" in result.stdout
    assert "No critical or high" in result.stdout
    assert result.stdout.index("No critical") < result.stdout.index(
        "suppressed vulnerabilities")
    assert "not-fixed" in result.stdout


def test_summary_keeps_fix_state_of_ignore_rules(tmp_path):
    # Grype's own ignore rules (e.g. for kernel headers) aren't VEX
    headers = grype_match("linux-libc-dev", fix_state="fixed",
                          versions=["6.12.1-1"])
    rule = {"match-type": "exact-indirect-match"}
    data = {"matches": [grype_match(fix_state="wont-fix")],
            "ignoredMatches": [{"match": headers,
                                "appliedIgnoreRules": [rule]}]}
    result = script("grype-vulnerability-summary.py",
                    write(tmp_path, "grype.json", data))
    assert result.returncode == 0, result.stderr
    # Debian wont-fix findings stay open and untriaged without a statement
    assert "Total unique vulnerabilities: **1**" in result.stdout
    assert "Untriaged (no VEX statement): **1**" in result.stdout
    assert "wont-fix" in result.stdout
    row = next(line for line in result.stdout.splitlines()
               if "linux-libc-dev" in line)
    assert "`6.12.1-1`" in row
    assert "ignore rule" in row


def test_summary_triage_escapes_markdown_table_text(tmp_path):
    stmt = statement("affected", ["pkg:deb/debian/openssl"],
                     action_statement="patch | investigate\n<next>")
    match = grype_match()
    result = script("grype-vulnerability-summary.py",
                    write(tmp_path, "grype.json", {"matches": [match]}),
                    "--triage", write(tmp_path, "triage.json", {
                        "matches": [vex_matched(match, stmt)]}))
    assert result.returncode == 0, result.stderr
    row = next(line for line in result.stdout.splitlines()
               if line.startswith("| CVE-"))
    assert row.count("|") == 6
    assert "affected: patch &#124; investigate<br>&lt;next&gt;" in row
    assert "Untriaged (no VEX statement): **0**" in result.stdout


def test_summary_triage_per_package(tmp_path):
    # the triage scan's statements apply to the same vulnerability and
    # artifact only; a match our statement re-added carries it already
    stmt = statement("under_investigation", ["pkg:deb/debian/openssl"])
    openssl, libssl = grype_match(), grype_match("libssl3t64")
    headers = grype_match("linux-libc-dev", vuln_id="CVE-2026-1111")
    readded = vex_matched(headers, statement(
        "affected", ["pkg:deb/debian/linux-libc-dev"], name="CVE-2026-1111",
        action_statement="x"))
    other = grype_match(vuln_id="CVE-2026-99999")
    data = {"matches": [openssl, libssl, readded, other]}
    triage = {"matches": [vex_matched(openssl, stmt)],
              "ignoredMatches": [libssl, other]}
    result = script("grype-vulnerability-summary.py",
                    write(tmp_path, "grype.json", data),
                    "--triage", write(tmp_path, "triage.json", triage))
    assert result.returncode == 0, result.stderr
    rows = {line.split(" | ")[0][2:]: line
            for line in result.stdout.splitlines()
            if line.startswith("| CVE-")}
    assert rows["CVE-2026-12345"].endswith(
        "| untriaged; under_investigation |")
    assert rows["CVE-2026-1111"].endswith("| affected: x |")
    assert rows["CVE-2026-99999"].endswith("| untriaged |")
    assert "Untriaged (no VEX statement): **2**" in result.stdout


def test_summary_does_not_silently_ignore_unreadable_triage(tmp_path):
    result = script("grype-vulnerability-summary.py",
                    write(tmp_path, "grype.json", {"matches": []}),
                    "--triage", str(tmp_path / "missing.json"))
    assert result.returncode != 0
    assert "cannot read triage scan" in result.stderr
    assert "Vulnerability summary" not in result.stdout
