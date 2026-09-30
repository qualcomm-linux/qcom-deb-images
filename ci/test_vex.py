"""Tests for the VEX scripts, see docs/vex.md"""

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

import json
import copy
import os
import subprocess
import sys
import zipfile
from urllib.parse import quote

import pytest
from debian.debian_support import Version

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
        "purl": f"pkg:deb/debian/{quote(name)}@{quote(version)}"
                "?arch=arm64&distro=debian-13",
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


def advisory(fixed="0.4.7-0qli1~bpo13+1", ecosystem="Debian:13"):
    events = [{"introduced": "0"}]
    if fixed:
        events.append({"fixed": fixed})
    return {
        "id": "QLI-CVE-2026-12345",
        "aliases": ["CVE-2026-12345"],
        "published": "2026-09-30T00:00:00Z",
        "modified": "2026-09-30T00:00:00Z",
        "summary": "urm: out-of-bounds write",
        "severity": [{"type": "CVSS_V3", "score":
                      "CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H"}],
        "database_specific": {"severity": "High"},
        "affected": [{
            "package": {"ecosystem": ecosystem,
                        "name": "userspace-resource-manager"},
            "ranges": [{"type": "ECOSYSTEM", "events": events}],
        }],
        "references": [{"type": "ADVISORY", "url": "https://example.com/a"}],
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


@pytest.mark.parametrize("a,b,expected", [
    ("1.0", "1.0~rc1", 1),
    ("1:0.1", "2.0", 1),
    ("3.5.0-1qcom1", "3.5.0-1", 1),
    ("1.0-2-3", "1.0-2-3", 0),
    ("0.4.6-0qli2~bpo13+2", "0.4.7-0qli1~bpo13+1", -1),
    ("1.0+b1", "1.0", 1),
])
def test_debian_version_ordering(a, b, expected):
    a, b = Version(a), Version(b)
    assert (a > b) - (a < b) == expected
    assert (b > a) - (b < a) == -expected


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
    files = [
        write(tmp_path, "a.openvex.json", vex_doc(
            statement("not_affected", ["pkg:deb/debian/openssl"],
                      justification="component_not_present",
                      impact_statement="not built"))),
        write(tmp_path, "QLI-CVE-2026-12345.osv.json", advisory()),
    ]
    result = script("vex-check.py", *files)
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


def test_check_rejects_bad_advisory(tmp_path):
    bad = advisory()
    bad["affected"][0]["ranges"][0]["events"][1]["fixed"] = "not a version!"
    path = write(tmp_path, "QLI-CVE-2026-12345.osv.json", bad)
    result = script("vex-check.py", path)
    assert result.returncode == 1
    assert "invalid Debian version" in result.stdout

    wrong_name = write(tmp_path, "other.osv.json", advisory())
    assert script("vex-check.py", wrong_name).returncode == 1


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


def run_advisories(tmp_path, advisories, vex=None, grype=None, sbom=SBOM):
    args = ["--sbom", write(tmp_path, "sbom.json", sbom)]
    if vex is not None:
        args += ["--vex", write(tmp_path, "vex.json", vex)]
    if grype is not None:
        args += ["--grype", write(tmp_path, "grype.json", grype)]
    files = [write(tmp_path, f"adv{i}.json", a)
             for i, a in enumerate(advisories)]
    result = script("vex-advisories.py", *args, *files, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    out = {n: json.loads((tmp_path / n).read_text()) for n in (
        "rootfs-vulns.advisories.json",
        "rootfs-vulns.advisories.cyclonedx.json")}
    return result, out


def test_advisory_matches_source_version(tmp_path):
    result, out = run_advisories(tmp_path, [advisory()])
    matches = out["rootfs-vulns.advisories.json"]["matches"]
    # both binaries of the source package, including the one whose SBOM
    # entry has no explicit source
    assert sorted(m["artifact"]["name"] for m in matches) == \
        ["liburm1", "userspace-resource-manager"]
    vuln = matches[0]["vulnerability"]
    assert vuln["id"] == "CVE-2026-12345"
    assert vuln["severity"] == "High"
    assert vuln["fix"] == {"versions": ["0.4.7-0qli1~bpo13+1"],
                           "state": "fixed"}
    assert matches[0]["relatedVulnerabilities"][0]["id"] == \
        "QLI-CVE-2026-12345"

    cdx = out["rootfs-vulns.advisories.cyclonedx.json"]
    assert cdx["bomFormat"] == "CycloneDX"
    assert len(cdx["vulnerabilities"]) == 1
    assert len(cdx["vulnerabilities"][0]["affects"]) == 2
    assert len(cdx["components"]) == 2

    with zipfile.ZipFile(tmp_path / "rootfs-advisories.osv.zip") as z:
        assert z.namelist() == ["QLI-CVE-2026-12345.json"]


def test_advisory_not_matched_when_fixed_or_other_release(tmp_path):
    fixed = advisory(fixed="0.4.6-0qli2~bpo13+1")
    result, out = run_advisories(tmp_path, [fixed])
    assert out["rootfs-vulns.advisories.json"]["matches"] == []

    result, out = run_advisories(tmp_path, [advisory(ecosystem="Debian:14")])
    assert out["rootfs-vulns.advisories.json"]["matches"] == []

    result, out = run_advisories(
        tmp_path, [advisory(ecosystem="Debian:trixie")])
    assert len(out["rootfs-vulns.advisories.json"]["matches"]) == 2


def test_advisory_warns_on_unknown_package(tmp_path):
    sbom = {**SBOM, "artifacts": [deb("openssl", "3.5.0-1")]}
    result, out = run_advisories(tmp_path, [advisory()], sbom=sbom)
    assert "package not in this SBOM" in result.stdout


def test_advisory_applies_vex(tmp_path):
    merged = vex_doc(
        statement("not_affected", ["pkg:deb/debian/liburm1"],
                  justification="vulnerable_code_not_in_execute_path",
                  impact_statement="not reached"))
    merged["statements"][0]["products"][0]["@id"] = PRODUCT + "@b1"
    result, out = run_advisories(tmp_path, [advisory()], vex=merged)
    data = out["rootfs-vulns.advisories.json"]
    assert [m["artifact"]["name"] for m in data["matches"]] == \
        ["userspace-resource-manager"]
    ignored = data["ignoredMatches"]
    assert [m["artifact"]["name"] for m in ignored] == ["liburm1"]
    assert ignored[0]["appliedIgnoreRules"][0]["vex-status"] == "not_affected"

    # the open finding got an affected statement in the merged document
    updated = json.loads((tmp_path / "vex.json").read_text())
    added = updated["statements"][-1]
    assert added["status"] == "affected"
    assert added["action_statement"] == "Update to 0.4.7-0qli1~bpo13+1"
    assert added["products"][0]["@id"] == PRODUCT + "@b1"
    assert added["products"][0]["subcomponents"][0]["@id"] == \
        SBOM["artifacts"][0]["purl"]
    assert script("vex-check.py", "--sbom", str(tmp_path / "sbom.json"),
                  str(tmp_path / "vex.json")).returncode == 0


def test_advisory_without_fix_and_dedup_with_grype(tmp_path):
    result, out = run_advisories(tmp_path, [advisory(fixed=None)],
                                 vex=vex_doc())
    updated = json.loads((tmp_path / "vex.json").read_text())
    assert updated["statements"][0]["action_statement"] == \
        "No fix available yet"

    grype = {"matches": [{
        "vulnerability": {"id": "CVE-2026-12345"},
        "artifact": SBOM["artifacts"][1]}], "ignoredMatches": []}
    result, out = run_advisories(tmp_path, [advisory()], grype=grype)
    assert "already reported by Grype" in result.stdout
    assert [m["artifact"]["name"] for m in
            out["rootfs-vulns.advisories.json"]["matches"]] == \
        ["userspace-resource-manager"]


def test_advisory_restricted_to_binaries(tmp_path):
    restricted = advisory()
    restricted["affected"][0]["ecosystem_specific"] = {
        "binaries": ["liburm1"]}
    result, out = run_advisories(tmp_path, [restricted])
    assert [m["artifact"]["name"] for m in
            out["rootfs-vulns.advisories.json"]["matches"]] == ["liburm1"]

    restricted["affected"][0]["ecosystem_specific"] = {"binaries": "liburm1"}
    path = write(tmp_path, "QLI-CVE-2026-12345.osv.json", restricted)
    assert script("vex-check.py", path).returncode == 1


@pytest.mark.parametrize("version,events,matched", [
    ("0~rc1", [{"introduced": "0"}, {"fixed": "0"}], True),
    ("0", [{"introduced": "0"}, {"fixed": "0"}], False),
    ("1.0~rc1", [{"introduced": "1.0"}], False),
    ("1.0", [{"introduced": "1.0"}], True),
    ("1.0", [{"introduced": "0"}, {"last_affected": "1.0"}], True),
    ("1.0+b1", [{"introduced": "0"}, {"last_affected": "1.0"}], False),
    ("1.0", [{"fixed": "1.0-0"}, {"introduced": "1.0"}], False),
    ("1.0", [{"introduced": "1.0"}, {"fixed": "1.0-0"}], False),
    ("1.9", [{"introduced": "0"}, {"limit": "2.0"}], True),
    ("2.0", [{"introduced": "0"}, {"limit": "2.0"}], False),
    ("3.0", [{"introduced": "0"}, {"limit": "*"}], True),
    ("2.0", [{"introduced": "0"}, {"limit": "1.0"},
             {"limit": "3.0"}], True),
    ("3.0", [{"introduced": "0"}, {"limit": "1.0"},
             {"limit": "3.0"}], False),
])
def test_advisory_range_boundaries(tmp_path, version, events, matched):
    record = advisory()
    record["affected"][0]["ranges"][0]["events"] = events
    sbom = {**SBOM, "artifacts": [
        deb("liburm1", "5.0+b1", "userspace-resource-manager", version)]}
    _, out = run_advisories(tmp_path, [record], sbom=sbom)
    assert bool(out["rootfs-vulns.advisories.json"]["matches"]) == matched


def test_advisory_does_not_recommend_obsolete_or_affected_fixes(tmp_path):
    record = advisory()
    record["affected"][0]["ranges"][0]["events"] = [
        {"introduced": "0"}, {"fixed": "0.4.0"},
        {"introduced": "0.4.5"}, {"fixed": "0.4.7"}]
    _, out = run_advisories(tmp_path, [record], vex=vex_doc())
    assert out["rootfs-vulns.advisories.json"]["matches"][0][
        "vulnerability"]["fix"]["versions"] == ["0.4.7"]
    other = copy.deepcopy(record["affected"][0])
    other["ranges"] = [{"type": "ECOSYSTEM", "events": [
        {"introduced": "0.4.5"}, {"fixed": "0.4.8"}]}]
    record["affected"].append(other)
    _, out = run_advisories(tmp_path, [record], vex=vex_doc())
    matches = out["rootfs-vulns.advisories.json"]["matches"]
    assert len(matches) == 2
    assert all(m["vulnerability"]["fix"]["versions"] == ["0.4.8"]
               for m in matches)
    assert script("vex-check.py", "--sbom", str(tmp_path / "sbom.json"),
                  str(tmp_path / "vex.json")).returncode == 0


def test_advisory_duplicate_entries_and_component_identity(tmp_path):
    record = advisory()
    record["affected"].append(copy.deepcopy(record["affected"][0]))
    sbom = {**SBOM, "artifacts": [
        deb("urm+addon", "0.4.6+qli1", "userspace-resource-manager")]}
    _, out = run_advisories(tmp_path, [record], vex=vex_doc(), sbom=sbom)
    matches = out["rootfs-vulns.advisories.json"]["matches"]
    assert len(matches) == 1
    updated = json.loads((tmp_path / "vex.json").read_text())
    subs = updated["statements"][0]["products"][0]["subcomponents"]
    assert subs == [{"@id": sbom["artifacts"][0]["purl"]}]
    cdx = out["rootfs-vulns.advisories.cyclonedx.json"]
    assert cdx["vulnerabilities"][0]["affects"] == [
        {"ref": sbom["artifacts"][0]["purl"]}]
    matchers = {m["matcher"] for m in matches[0]["matchDetails"]}
    assert "openvex-matcher" in matchers
    assert script("vex-check.py", "--sbom", str(tmp_path / "sbom.json"),
                  str(tmp_path / "vex.json")).returncode == 0
    _, repeated = run_advisories(tmp_path, [record], vex=updated, sbom=sbom)
    assert repeated["rootfs-vulns.advisories.json"] == \
        out["rootfs-vulns.advisories.json"]


def test_advisory_dedup_uses_all_aliases_and_exact_component(tmp_path):
    record = advisory()
    record["aliases"].append("CVE-2026-54321")
    other_arch = copy.deepcopy(SBOM["artifacts"][1])
    other_arch["purl"] = other_arch["purl"].replace("arm64", "amd64")
    sbom = {**SBOM, "artifacts": [SBOM["artifacts"][1], other_arch]}
    grype = {"ignoredMatches": [{
        "vulnerability": {"id": "CVE-2026-54321"},
        "artifact": SBOM["artifacts"][1],
    }]}
    result, out = run_advisories(tmp_path, [record], grype=grype, sbom=sbom)
    assert "already reported by Grype" in result.stdout
    matches = out["rootfs-vulns.advisories.json"]["matches"]
    assert [m["artifact"]["purl"] for m in matches] == [other_arch["purl"]]


def test_withdrawn_and_empty_advisories(tmp_path):
    record = {
        "id": "QLI-2026-1234",
        "modified": "2026-09-30T01:00:00Z",
        "withdrawn": "2026-09-30T01:00:00Z",
    }
    path = write(tmp_path, "QLI-2026-1234.osv.json", record)
    assert script("vex-check.py", path).returncode == 0
    for records in ([], [record]):
        _, out = run_advisories(tmp_path, records, vex=vex_doc())
        assert out["rootfs-vulns.advisories.json"]["matches"] == []
        assert out["rootfs-vulns.advisories.cyclonedx.json"][
            "vulnerabilities"] == []
        with zipfile.ZipFile(tmp_path / "rootfs-advisories.osv.zip") as bundle:
            assert bundle.namelist() == []


@pytest.mark.parametrize("events", [
    [], [None], [{"introduced": 0}],
    [{"introduced": "0", "fixed": "1.0"}],
    [{"introduced": "0"}, {"unknown": "1.0"}],
    [{"introduced": "0"}, {"fixed": None}],
    [{"fixed": "1.0"}],
    [{"introduced": "0"}, {"fixed": "2.0"}, {"last_affected": "1.0"}],
    [{"introduced": "0"}, {"limit": "bad version!"}],
])
def test_check_rejects_invalid_range_events(tmp_path, events):
    record = advisory()
    record["affected"][0]["ranges"][0]["events"] = events
    path = write(tmp_path, "QLI-CVE-2026-12345.osv.json", record)
    result = script("vex-check.py", path)
    assert result.returncode == 1
    assert "error:" in result.stdout
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("field,value", [
    ("id", 42), ("summary", []), ("aliases", [{}]),
    ("aliases", ["CVE-2026-12345", "CVE-not-real"]),
    ("database_specific", []), ("affected", [None]),
    ("references", [{"url": 1}]), ("severity", [{"score": []}]),
    ("withdrawn", "not a timestamp"),
])
def test_check_malformed_advisories(tmp_path, field, value):
    record = advisory()
    record[field] = value
    path = write(tmp_path, "QLI-CVE-2026-12345.osv.json", record)
    result = script("vex-check.py", path)
    assert result.returncode == 1
    assert "error:" in result.stdout
    assert "Traceback" not in result.stderr


def test_check_duplicate_advisory_aliases(tmp_path):
    first, second = advisory(), advisory()
    second["id"] = "QLI-2026-1234"
    paths = [write(tmp_path, f"{record['id']}.osv.json", record)
             for record in (first, second)]
    result = script("vex-check.py", *paths)
    assert result.returncode == 1
    assert "already recorded" in result.stdout
    result = script("vex-advisories.py", "--sbom",
                    write(tmp_path, "sbom.json", SBOM), *paths, cwd=tmp_path)
    assert result.returncode != 0
    assert "duplicate advisory IDs/aliases" in result.stderr


@pytest.mark.parametrize("investigating_all", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_cyclonedx_triage_is_order_independent(
        tmp_path, investigating_all, reverse):
    doc = vex_doc(statement("under_investigation",
                            ["pkg:deb/debian/liburm1"]))
    if investigating_all:
        doc["statements"].append(statement("under_investigation", [
            "pkg:deb/debian/userspace-resource-manager"]))
    sbom = copy.deepcopy(SBOM)
    if reverse:
        sbom["artifacts"].reverse()
    _, out = run_advisories(tmp_path, [advisory()], vex=doc, sbom=sbom)
    state = out["rootfs-vulns.advisories.cyclonedx.json"][
        "vulnerabilities"][0]["analysis"]["state"]
    assert state == ("in_triage" if investigating_all else "exploitable")


def test_generated_actions_are_specific_to_each_package(tmp_path):
    record = advisory()
    second = copy.deepcopy(record["affected"][0])
    second["package"]["name"] = "openssl"
    second["ranges"][0]["events"][-1]["fixed"] = "3.6.0-1"
    record["affected"].append(second)
    _, out = run_advisories(tmp_path, [record], vex=vex_doc())
    updated = json.loads((tmp_path / "vex.json").read_text())
    assert {stmt["action_statement"] for stmt in updated["statements"]} == {
        "Update to 0.4.7-0qli1~bpo13+1", "Update to 3.6.0-1"}
    cdx = out["rootfs-vulns.advisories.cyclonedx.json"]
    recommendation = cdx["vulnerabilities"][0]["recommendation"]
    assert "openssl: Update to 3.6.0-1" in recommendation
    assert "userspace-resource-manager: Update to 0.4.7-0qli1~bpo13+1" in \
        recommendation
    assert script("vex-check.py", "--sbom", str(tmp_path / "sbom.json"),
                  str(tmp_path / "vex.json")).returncode == 0


def test_advisory_cve_identity_is_stable(tmp_path):
    record = advisory()
    record["aliases"].insert(0, "CVE-2026-11111")
    _, out = run_advisories(tmp_path, [record])
    assert {finding["vulnerability"]["id"] for finding in
            out["rootfs-vulns.advisories.json"]["matches"]} == {
                "CVE-2026-12345"}
