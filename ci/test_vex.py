"""Tests for the VEX scripts"""

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

import json
import copy
import os
import subprocess
import sys

import pytest

SCRIPTS = os.path.join(os.path.dirname(__file__), os.pardir, "scripts")
sys.path.insert(0, SCRIPTS)

import vexlib  # noqa: E402  # pylint: disable=import-error

PRODUCT = vexlib.PRODUCT


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


SBOM = {
    "source": {"name": "qualcomm-linux-debian-rootfs", "version": "b1"},
    "distro": {"id": "debian", "versionID": "13", "versionCodename": "trixie"},
    "artifacts": [
        deb("userspace-resource-manager", "0.4.6-0qli2~bpo13+2"),
        deb("liburm1", "0.4.6-0qli2~bpo13+2",
            "userspace-resource-manager", None),
        deb("libssl3t64", "3.5.0-1", "openssl"),
        deb("openssl", "3.5.0-1"),
    ],
}


def statement(status, subs, name="CVE-2026-12345", **extra):
    return {
        "vulnerability": {"name": name},
        "products": [{"@id": PRODUCT,
                      "subcomponents": [{"@id": s} for s in subs]}],
        "status": status, **extra,
    }


def vex_doc(*statements):
    return {
        "@context": vexlib.OPENVEX_CONTEXT,
        "@id": "https://example.com/vex/test",
        "author": "test", "timestamp": "2026-09-30T00:00:00Z", "version": 1,
        "statements": list(statements),
    }


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return str(path)


def test_purl_matching():
    actual = "pkg:deb/debian/libssl3t64@3.5.0-1?arch=arm64&distro=debian-13"
    assert vexlib.purl_matches("pkg:deb/debian/libssl3t64", actual)
    assert vexlib.purl_matches("pkg:deb/debian/libssl3t64@3.5.0-1", actual)
    assert vexlib.purl_matches("pkg:deb/debian/libssl3t64?arch=arm64", actual)
    assert not vexlib.purl_matches("pkg:deb/debian/libssl3t64@3.5.1-1",
                                   actual)
    assert not vexlib.purl_matches("pkg:deb/debian/openssl", actual)
    assert not vexlib.purl_matches("pkg:deb/debian/libssl3t64?arch=amd64",
                                   actual)


def test_check_accepts_valid_files(tmp_path):
    path = write(tmp_path, "a.openvex.json", vex_doc(
        statement("not_affected", ["pkg:deb/debian/openssl"],
                  justification="component_not_present",
                  impact_statement="not built")))
    result = script("vex-check.py", path)
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize("bad", [
    statement("not_affected", ["pkg:deb/debian/openssl"]),
    statement("fixed", ["pkg:deb/debian/openssl"]),
    statement("affected", ["pkg:deb/debian/openssl"]),
    statement("affected", ["pkg:generic/openssl"], action_statement="x"),
    statement("affected", ["pkg:deb/debian/openssl"], name="bug-1",
              action_statement="x"),
])
def test_check_rejects_rule_violations(tmp_path, bad):
    path = write(tmp_path, "a.openvex.json", vex_doc(bad))
    result = script("vex-check.py", path)
    assert result.returncode == 1
    assert "error:" in result.stdout


def test_check_rejects_versioned_product_and_duplicates(tmp_path):
    stmt = statement("affected", ["pkg:deb/debian/openssl"],
                     action_statement="patch it")
    pinned = json.loads(json.dumps(stmt))
    pinned["products"][0]["@id"] = PRODUCT + "@b1"
    path = write(tmp_path, "a.openvex.json", vex_doc(pinned))
    assert script("vex-check.py", path).returncode == 1

    doc = write(tmp_path, "b.openvex.json", vex_doc(stmt, stmt))
    result = script("vex-check.py", doc)
    assert result.returncode == 1
    assert "already covered" in result.stdout


def test_check_sbom_coverage(tmp_path):
    doc = write(tmp_path, "a.openvex.json", vex_doc(
        statement("not_affected", ["pkg:deb/debian/openssl",
                                   "pkg:deb/debian/gone"],
                  justification="component_not_present",
                  impact_statement="not built"),
        statement("affected", ["pkg:deb/debian/openssl@1.0-1"],
                  name="CVE-2026-1111", action_statement="x"),
        statement("affected", ["pkg:deb/debian/libssl3t64"],
                  name="CVE-2026-1112", action_statement="x")))
    sbom = write(tmp_path, "sbom.json", SBOM)
    result = script("vex-check.py", "--sbom", sbom, doc)
    assert result.returncode == 0, result.stdout
    assert "gone matches nothing" in result.stdout
    assert "openssl@1.0-1 matches nothing" in result.stdout
    assert "src:openssl not covered: libssl3t64" in result.stdout


def test_merge(tmp_path):
    doc = vex_doc(statement("affected", ["pkg:deb/debian/openssl"],
                            action_statement="x"))
    path = write(tmp_path, "a.openvex.json", doc)
    result = script("vex-merge.py", "--product-version", "b1", path)
    merged = json.loads(result.stdout)
    st = merged["statements"][0]
    assert st["products"][0]["@id"] == PRODUCT + "@b1"
    assert st["timestamp"] == "2026-09-30T00:00:00Z"
    assert script("vex-check.py", "--sbom", write(tmp_path, "s.json", SBOM),
                  write(tmp_path, "m.openvex.json", merged)).returncode == 0

    empty = json.loads(
        script("vex-merge.py", "--product-version", "b1").stdout)
    assert empty["statements"] == []


def test_fixed_requires_every_binary_to_be_pinned(tmp_path):
    stmt = statement("fixed", ["pkg:deb/debian/openssl@3.5.0-1",
                               "pkg:deb/debian/libssl3t64"])
    path = write(tmp_path, "fixed.json", vex_doc(stmt))
    result = script("vex-check.py", path)
    assert result.returncode == 1
    assert "every subcomponent pinned" in result.stdout
    stmt["products"][0]["subcomponents"][1]["@id"] += "@3.5.0-1"
    path = write(tmp_path, "fixed.json", vex_doc(stmt))
    assert script("vex-check.py", path).returncode == 0


@pytest.mark.parametrize("first,second,overlap", [
    ("openssl", "openssl@3.5.0-1", True),
    ("openssl@3.5.0-1", "openssl@3.5.1-1", False),
    ("libssl3t64", "openssl", False),
    ("openssl?arch=arm64", "openssl?distro=debian-13", True),
    ("openssl?arch=arm64", "openssl?arch=amd64", False),
    ("openssl@1.0%2Bqli1", "openssl@1.0+qli1", True),
    ("openssl?arch=arm64&distro=debian-13",
     "openssl?distro=debian-13&arch=arm64", True),
])
def test_check_overlapping_scopes(tmp_path, first, second, overlap):
    paths = []
    for index, package in enumerate((first, second)):
        stmt = statement("affected", [f"pkg:deb/debian/{package}"],
                         action_statement="patch it")
        paths.append(write(tmp_path, f"{index}.json", vex_doc(stmt)))
    result = script("vex-check.py", *paths)
    assert result.returncode == int(overlap), result.stdout
    assert ("already covered" in result.stdout) == overlap


@pytest.mark.parametrize("doc", [
    None, [], {"@context": vexlib.OPENVEX_CONTEXT},
    {**vex_doc(), "statements": {}},
    {**vex_doc(), "statements": [None]},
    {**vex_doc(), "version": True},
    {**vex_doc(), "author": []},
    vex_doc({"vulnerability": [], "products": [], "status": "affected"}),
    vex_doc(statement("affected", ["pkg:deb/debian/openssl"], name=1234)),
    vex_doc({**statement("affected", []), "products": [None]}),
    vex_doc(statement("affected", [1234], action_statement="patch")),
    vex_doc({**statement("affected", []),
             "vulnerability": {"name": "CVE-2026-12345", "aliases": [{}]}}),
])
def test_check_malformed_documents(tmp_path, doc):
    result = script("vex-check.py", write(tmp_path, "bad.json", doc))
    assert result.returncode == 1
    assert "error:" in result.stdout
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("timestamp", [
    "2026-09-30", "2026-09-30T00:00:00", "2026-09-30 00:00:00Z",
])
def test_check_requires_rfc3339_timestamps(tmp_path, timestamp):
    doc = vex_doc()
    doc["timestamp"] = timestamp
    result = script("vex-check.py", write(tmp_path, "date.json", doc))
    assert result.returncode == 1
    assert "RFC 3339" in result.stdout


def test_no_assessments(tmp_path):
    assert script("vex-check.py").returncode == 0
    empty = script("vex-merge.py", "--product-version", "b1")
    assert empty.returncode == 0
    merged = json.loads(empty.stdout)
    result = script("vex-check.py", "--sbom",
                    write(tmp_path, "sbom.json", SBOM),
                    write(tmp_path, "vex.json", merged))
    assert result.returncode == 0, result.stdout


def grype_match(name="openssl", severity="High", version="3.5.0-1",
                vuln_id="CVE-2026-12345", fix_state="not-fixed", versions=()):
    return {
        "vulnerability": {
            "id": vuln_id, "severity": severity,
            "fix": {"state": fix_state, "versions": list(versions)},
        },
        "artifact": deb(name, version),
    }


def test_summary_layout_and_suppressed_fix_versions(tmp_path):
    fixed = grype_match(versions=["99.0-1"])
    fixed["appliedIgnoreRules"] = [{"vex-status": "fixed"}]
    unaffected = grype_match("libssl3t64", versions=["99.0-1"])
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
    assert "99.0-1" not in text
    suppressed = next(line for line in text.splitlines()
                      if line.startswith("| CVE-2026-12345 |"))
    assert "`openssl 3.5.0-1`" in suppressed
    assert "`libssl3t64 3.5.0-1`" in suppressed
    assert "\U0001F7E2</span> `3.5.0-1`" in suppressed
    assert "\U0001F7E0</span> wont-fix" in suppressed
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
    assert "\U0001F7E0</span> wont-fix" in result.stdout


@pytest.mark.parametrize("state,versions,icon,label", [
    ("not-fixed", [], "\U0001F534", "not-fixed"),
    ("wont-fix", [], "\U0001F7E0", "wont-fix"),
    ("fixed", ["3.5.1-1"], "\U0001F7E2", "`3.5.1-1`"),
    ("unknown", [], "\u2754", "unknown"),
])
def test_summary_fix_icons(tmp_path, state, versions, icon, label):
    data = {"matches": [grype_match(fix_state=state, versions=versions)]}
    result = script("grype-vulnerability-summary.py",
                    write(tmp_path, "grype.json", data))
    assert result.returncode == 0, result.stderr
    assert f"{icon}</span> {label}" in result.stdout
    assert "<span title=" in result.stdout


def test_summary_triage_escapes_markdown_table_text(tmp_path):
    stmt = statement("affected", ["pkg:deb/debian/openssl"],
                     action_statement="patch | investigate\n<next>")
    result = script("grype-vulnerability-summary.py",
                    write(tmp_path, "grype.json",
                          {"matches": [grype_match()]}),
                    "--vex", write(tmp_path, "vex.json", vex_doc(stmt)))
    assert result.returncode == 0, result.stderr
    row = next(line for line in result.stdout.splitlines()
               if line.startswith("| CVE-"))
    assert row.count("|") == 6
    assert "affected: patch &#124; investigate<br>&lt;next&gt;" in row
    assert "Untriaged (no VEX statement): **0**" in result.stdout


def test_summary_does_not_silently_ignore_unreadable_vex(tmp_path):
    result = script("grype-vulnerability-summary.py",
                    write(tmp_path, "grype.json", {"matches": []}),
                    "--vex", str(tmp_path / "missing.json"))
    assert result.returncode != 0
    assert "cannot read VEX" in result.stderr
    assert "Vulnerability summary" not in result.stdout
