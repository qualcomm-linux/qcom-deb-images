"""Tests for the VEX scripts"""

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

import json
import os
import shutil
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


@pytest.mark.parametrize("purl,parsed", [
    ("pkg:deb/debian/libssl3t64", ("libssl3t64", None)),
    ("pkg:deb/debian/libstdc++6@1:14.2.0-19~bpo13+1",
     ("libstdc++6", "1:14.2.0-19~bpo13+1")),
    # only the plain form Debian writes, see vex/README.md
    ("pkg:deb/debian/libssl3t64?arch=arm64", None),
    ("pkg:deb/debian/libssl3t64#usr/lib", None),
    ("pkg:deb/debian/libstdc%2B%2B6", None),
    ("pkg:deb/debian/LibSSL3t64", None),
    ("pkg:deb/ubuntu/libssl3t64", None),
    ("pkg:deb/debian/libssl3t64@", None),
    ("pkg:deb/debian/@3.5.0-1", None),
    (None, None),
])
def test_parse_subcomponent(purl, parsed):
    assert vexlib.parse_subcomponent(purl) == parsed


def test_check_accepts_valid_files(tmp_path):
    path = write(tmp_path, "a.openvex.json", vex_doc(
        statement("not_affected", ["pkg:deb/debian/openssl"],
                  justification="component_not_present",
                  impact_statement="not built")))
    result = script("vex-check.py", path)
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize("contents", [None, "not JSON"])
def test_check_reports_unavailable_schema(tmp_path, contents):
    schema = tmp_path / "schema.json"
    if contents is not None:
        schema.write_text(contents)
    result = script("vex-check.py", "--schema", str(schema))
    assert result.returncode == 2
    assert "cannot read OpenVEX schema" in result.stderr
    assert "make vex-schema" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("bad", [
    statement("not_affected", ["pkg:deb/debian/openssl"]),
    statement("fixed", ["pkg:deb/debian/openssl"]),
    statement("affected", ["pkg:deb/debian/openssl"]),
    statement("affected", ["pkg:generic/openssl"], action_statement="x"),
    statement("affected", ["pkg:deb/debian/openssl?arch=arm64"],
              action_statement="x"),
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

    other = {**stmt, "action_statement": "patch it differently"}
    doc = write(tmp_path, "b.openvex.json", vex_doc(stmt, other))
    result = script("vex-check.py", doc)
    assert result.returncode == 1
    assert "already covered" in result.stdout


@pytest.mark.parametrize("extra_args", [[], ["--coverage-only"]])
def test_check_sbom_coverage(tmp_path, extra_args):
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
    result = script("vex-check.py", *extra_args, "--sbom", sbom, doc)
    assert result.returncode == 0, result.stdout
    assert "gone matches nothing" in result.stdout
    assert "openssl@1.0-1 matches nothing" in result.stdout
    assert "src:openssl not covered: libssl3t64" in result.stdout


def test_coverage_only_without_schema_or_site_packages(tmp_path):
    # Reproduce the image's sparse checkout: only the checker and its helper.
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("vex-check.py", "vexlib.py"):
        shutil.copy(os.path.join(SCRIPTS, name), scripts / name)
    doc = write(tmp_path, "a.openvex.json", vex_doc(
        statement("affected", ["pkg:deb/debian/openssl"],
                  action_statement="patch it")))
    sbom = write(tmp_path, "sbom.json", SBOM)
    result = subprocess.run(
        [sys.executable, "-S", str(scripts / "vex-check.py"),
         "--coverage-only", "--sbom", sbom, doc],
        capture_output=True, text=True, check=False, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "src:openssl not covered: libssl3t64" in result.stdout


def test_coverage_only_requires_sbom():
    result = script("vex-check.py", "--coverage-only")
    assert result.returncode == 2
    assert "--coverage-only requires --sbom" in result.stderr


@pytest.mark.parametrize("version", ["", "@3.5.0-1"])
def test_coverage_only_fully_covered(tmp_path, version):
    doc = write(tmp_path, "a.openvex.json", vex_doc(
        statement("affected", [f"pkg:deb/debian/openssl{version}",
                               f"pkg:deb/debian/libssl3t64{version}"],
                  action_statement="patch it")))
    result = script("vex-check.py", "--coverage-only", "--sbom",
                    write(tmp_path, "sbom.json", SBOM), doc)
    assert result.returncode == 0, result.stderr
    assert not result.stdout


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
    ("openssl@3.5.0-1", "openssl@3.5.0-1", True),
    ("libssl3t64", "openssl", False),
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
    # unknown fields, e.g. typos, are rejected by the OpenVEX schema
    vex_doc(statement("not_affected", ["pkg:deb/debian/openssl"],
                      justifcation="component_not_present",
                      impact_statement="not built")),
    {**vex_doc(statement("affected", ["pkg:deb/debian/openssl"],
                         action_statement="patch it")), "auther": "test"},
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
    doc = vex_doc(statement("affected", ["pkg:deb/debian/openssl"],
                            action_statement="patch it"))
    doc["timestamp"] = timestamp
    result = script("vex-check.py", write(tmp_path, "date.json", doc))
    assert result.returncode == 1
    assert "RFC 3339" in result.stdout


@pytest.mark.parametrize("extra_args", [[], ["--coverage-only"]])
def test_no_assessments(tmp_path, extra_args):
    assert script("vex-check.py").returncode == 0
    result = script("vex-check.py", *extra_args, "--sbom",
                    write(tmp_path, "sbom.json", SBOM))
    assert result.returncode == 0, result.stdout
    assert not result.stdout


# --- OSV advisories ---

def advisory(advisory_id="QLI-2026-1", **extra):
    doc = {
        "id": advisory_id,
        "published": "2026-09-30T00:00:00Z",
        "modified": "2026-09-30T00:00:00Z",
        "summary": "urm: out-of-bounds write",
        "database_specific": {"severity": "High"},
        "affected": [{
            "package": {"ecosystem": "Debian:13",
                        "name": "userspace-resource-manager"},
            "ranges": [{"type": "ECOSYSTEM", "events": [
                {"introduced": "0"}, {"fixed": "0.4.7-0qli1~bpo13+1"}]}],
        }],
    }
    doc.update(extra)
    return doc


def write_advisory(tmp_path, doc, name=None):
    return write(tmp_path, name or f"{doc['id']}.osv.json", doc)


@pytest.mark.parametrize("a,b,result", [
    ("1.0", "1.0", 0),
    ("1.0~rc1", "1.0", -1),
    ("1.0", "1.0+b1", -1),
    ("1.0", "1.0-1", -1),
    ("1.9", "1.10", -1),
    ("1:0.1", "2.0", 1),
    ("0.4.6-0qli2~bpo13+2", "0.4.6-0qli2", -1),
    ("3.5.4-1~deb13u2", "3.5.4-1~deb13u1", 1),
    ("1.0a", "1.0+", -1),
    ("1.0~~", "1.0~", -1),
    ("00.1", "0.01", 0),
])
def test_compare_versions(a, b, result):
    assert vexlib.compare_versions(a, b) == result
    assert vexlib.compare_versions(b, a) == -result


@pytest.mark.parametrize("events,pairs", [
    ([{"introduced": "0"}], [("0", None, None)]),
    ([{"introduced": "0"}, {"fixed": "1.2-1"}],
     [("0", "fixed", "1.2-1")]),
    ([{"introduced": "1.0-1"}, {"last_affected": "1.1-1"},
      {"introduced": "2.0-1"}],
     [("1.0-1", "last_affected", "1.1-1"), ("2.0-1", None, None)]),
])
def test_range_pairs(events, pairs):
    assert vexlib.range_pairs({"type": "ECOSYSTEM", "events": events}) == \
        pairs


@pytest.mark.parametrize("osv_range", [
    {"type": "SEMVER", "events": [{"introduced": "0"}]},
    {"type": "ECOSYSTEM", "events": []},
    {"type": "ECOSYSTEM", "events": [{"fixed": "1.0"}]},
    {"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"limit": "*"}]},
    {"type": "ECOSYSTEM", "events": [{"introduced": "1.0", "fixed": "2"}]},
    {"type": "ECOSYSTEM", "events": [{"introduced": "1.0"},
                                     {"introduced": "2.0"}]},
    {"type": "ECOSYSTEM", "events": [{"introduced": "2.0"},
                                     {"fixed": "1.0"}]},
    {"type": "ECOSYSTEM", "events": [{"introduced": "1.0"},
                                     {"fixed": "2.0"},
                                     {"introduced": "0"}]},
    {"type": "ECOSYSTEM", "events": [{"introduced": "v1"}]},
    {"type": "ECOSYSTEM", "events": [{"introduced": "1.0-"}]},
    # dpkg rejects colons without an epoch
    {"type": "ECOSYSTEM", "events": [{"introduced": "0"},
                                     {"fixed": "1.0:5"}]},
])
def test_range_pairs_rejects(osv_range):
    with pytest.raises(ValueError):
        vexlib.range_pairs(osv_range)


def test_check_accepts_advisories_and_their_statements(tmp_path):
    paths = [
        write_advisory(tmp_path, advisory()),
        write_advisory(tmp_path, advisory(
            "QLI-CVE-2026-12345", aliases=["CVE-2026-12345"],
            severity=[{"type": "CVSS_V3", "score":
                       "CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H"}],
            references=[{"type": "WEB", "url": "https://example.com"}])),
        write_advisory(tmp_path, {"id": "QLI-2026-2",
                                  "modified": "2026-10-01T00:00:00Z",
                                  "withdrawn": "2026-10-01T00:00:00Z"}),
        write(tmp_path, "a.openvex.json", vex_doc(
            statement("not_affected", ["pkg:deb/debian/liburm1"],
                      name="QLI-2026-1",
                      justification="vulnerable_code_not_present",
                      impact_statement="not built"),
            statement("affected", ["pkg:deb/debian/liburm1"],
                      name="CVE-2026-12345", action_statement="update"))),
    ]
    result = script("vex-check.py", *paths)
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize("doc,name,message", [
    (advisory("GHSA-1234"), None, "must be QLI-<CVE>"),
    (advisory(), "QLI-2026-2.osv.json", "file name must be"),
    (advisory(published="2026-09-30"), None, "published"),
    (advisory(summary=" "), None, "summary"),
    (advisory(database_specific={}), None, "severity must be one of"),
    (advisory(database_specific={"severity": "high"}), None,
     "severity must be one of"),
    (advisory(severity=[{"type": "Ubuntu", "score": "high"}]), None,
     "severity type"),
    (advisory("QLI-CVE-2026-1234"), None, "aliases must include"),
    (advisory("QLI-CVE-2026-1234",
              aliases=["CVE-2026-1234", "CVE-2026-9999"]), None,
     "no other CVE"),
    (advisory(aliases=["CVE-2026-1234"]), None, "rename the advisory"),
    (advisory(aliases=["QLI-2026-7"]), None, "is a QLI advisory"),
    (advisory(affected=[]), None, "no affected packages"),
    (advisory(typo=1), None, "Additional properties"),
])
def test_check_rejects_advisories(tmp_path, doc, name, message):
    result = script("vex-check.py", write_advisory(tmp_path, doc, name))
    assert result.returncode == 1
    assert message in result.stdout
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("change,message", [
    ({"package": {"ecosystem": "Ubuntu:24.04",
                  "name": "userspace-resource-manager"}}, "ecosystem must"),
    ({"package": {"ecosystem": "Debian:13x",
                  "name": "userspace-resource-manager"}},
     "unknown Debian release '13x'"),
    ({"package": {"ecosystem": "Debian:99",
                  "name": "userspace-resource-manager"}},
     "unknown Debian release '99'"),
    # the OSV schema wants the version of releases that have one
    ({"package": {"ecosystem": "Debian:trixie",
                  "name": "userspace-resource-manager"}},
     "by its version: Debian:13"),
    ({"package": {"ecosystem": "Debian:forky",
                  "name": "userspace-resource-manager"}},
     "by its version: Debian:14"),
    ({"package": {"ecosystem": "Debian", "name": "URM"}},
     "source package"),
    ({"versions": ["1.0"]}, "versions is not supported"),
    ({"ecosystem_specific": {"binaries": ["liburm1"]}},
     "covers every binary of the source package"),
    ({"ranges": []}, "no ranges"),
    ({"ranges": [{"type": "ECOSYSTEM", "events": [
        {"introduced": "0"}, {"limit": "*"}]}]}, "unsupported range event"),
    ({"ranges": [{"type": "ECOSYSTEM", "events": [
        {"introduced": "2.0"}, {"fixed": "1.0"}]}]}, "ascending"),
    # the OSV schema doesn't allow mixing these
    ({"ranges": [{"type": "ECOSYSTEM", "events": [
        {"introduced": "1.0"}, {"last_affected": "1.1"},
        {"introduced": "2.0"}, {"fixed": "2.1"}]}]}, "should not be valid"),
])
def test_check_rejects_affected(tmp_path, change, message):
    doc = advisory()
    doc["affected"][0].update(change)
    result = script("vex-check.py", write_advisory(tmp_path, doc))
    assert result.returncode == 1
    assert message in result.stdout


@pytest.mark.parametrize("ecosystem", [
    "Debian", "Debian:13", "Debian:14", "Debian:sid",
])
def test_check_accepts_debian_releases(tmp_path, ecosystem):
    doc = advisory()
    doc["affected"][0]["package"]["ecosystem"] = ecosystem
    result = script("vex-check.py", write_advisory(tmp_path, doc))
    assert result.returncode == 0, result.stdout


def test_check_reports_unavailable_distro_info(tmp_path):
    result = script("vex-check.py", "--distro-info",
                    str(tmp_path / "missing.csv"),
                    write_advisory(tmp_path, advisory()))
    assert result.returncode == 2
    assert "cannot read the Debian releases" in result.stderr
    assert "distro-info-data" in result.stderr


@pytest.mark.parametrize("first,second", [
    (advisory(), advisory()),
    (advisory(aliases=["GHSA-aaaa-bbbb-cccc"]),
     advisory("QLI-2026-2", aliases=["GHSA-aaaa-bbbb-cccc"])),
])
def test_check_rejects_duplicate_advisory_ids(tmp_path, first, second):
    sub = tmp_path / "sub"
    sub.mkdir()
    result = script("vex-check.py", write_advisory(tmp_path, first),
                    write_advisory(sub, second))
    assert result.returncode == 1
    assert "is already claimed by" in result.stdout


@pytest.mark.parametrize("name,message", [
    ("QLI-CVE-2026-12345", "must be CVE-2026-12345"),
    ("QLI-2026-404", "no such advisory"),
])
def test_check_rejects_statements_about_advisories(tmp_path, name, message):
    doc = write(tmp_path, "a.openvex.json", vex_doc(
        statement("affected", ["pkg:deb/debian/liburm1"], name=name,
                  action_statement="update")))
    result = script("vex-check.py", write_advisory(tmp_path, advisory()),
                    doc)
    assert result.returncode == 1
    assert message in result.stdout


def test_check_reports_unavailable_osv_schema(tmp_path):
    result = script("vex-check.py", "--osv-schema",
                    str(tmp_path / "missing.json"),
                    write_advisory(tmp_path, advisory()))
    assert result.returncode == 2
    assert "cannot read OSV schema" in result.stderr
    assert "make osv-schema" in result.stderr


def test_coverage_only_ignores_advisories(tmp_path):
    result = script("vex-check.py", "--coverage-only", "--osv-schema",
                    str(tmp_path / "missing.json"), "--sbom",
                    write(tmp_path, "sbom.json", SBOM),
                    write_advisory(tmp_path, advisory(typo=1)))
    assert result.returncode == 0, result.stdout
    assert not result.stdout
