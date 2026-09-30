"""Tests for the VEX scripts"""

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

import json
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


def test_no_assessments(tmp_path):
    assert script("vex-check.py").returncode == 0
    result = script("vex-check.py", "--sbom",
                    write(tmp_path, "sbom.json", SBOM))
    assert result.returncode == 0, result.stdout
    assert not result.stdout
