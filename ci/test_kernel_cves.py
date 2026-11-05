"""Tests for the kernel CVE assessment"""

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

import importlib.util
import json
import os
import subprocess
import sys

import pytest

SCRIPTS = os.path.join(os.path.dirname(__file__), os.pardir, "scripts")
SCRIPT = os.path.join(SCRIPTS, "kernel-cves.py")

spec = importlib.util.spec_from_file_location("kernel_cves", SCRIPT)
kernel_cves = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kernel_cves)

QLI_VERSION = "7.2.0+git20261008~gb8f0c6b1ab06-0qli1~bpo13+1"


def semver(introduced, *fixes, mainline=None):
    # the semver block of a vulns.git record: introduced in a version, fixed
    # in stable releases and in a mainline release
    versions = [{"version": introduced, "status": "affected"},
                {"version": "0", "lessThan": introduced,
                 "status": "unaffected", "versionType": "semver"}]
    for fix in fixes:
        branch = ".".join(fix.split(".")[:2])
        versions.append({"version": fix, "lessThanOrEqual": f"{branch}.*",
                         "status": "unaffected", "versionType": "semver"})
    if mainline:
        versions.append({"version": mainline, "lessThanOrEqual": "*",
                         "status": "unaffected",
                         "versionType": "original_commit_for_fix"})
    return versions


def record(cve_id, versions, files, default="affected", severity=None,
           fixes=()):
    cna = {
        "descriptions": [{"lang": "en", "value":
                          "In the Linux kernel, the following vulnerability "
                          "has been resolved:\n\nfoo: fix bar\n\nDetails."}],
        "affected": [{
            "product": "Linux", "vendor": "Linux",
            "defaultStatus": default, "programFiles": files,
            "versions": versions,
        }],
        "references": [{"url": f"https://git.kernel.org/stable/c/{cve_id}"}],
    }
    if fixes:
        # the git ranges, one per mainline or stable fix commit
        cna["affected"].insert(0, {
            "product": "Linux", "vendor": "Linux",
            "defaultStatus": "unaffected", "programFiles": files,
            "versions": [{"version": "1" * 40, "lessThan": fix,
                          "status": "affected", "versionType": "git"}
                         for fix in fixes],
        })
    if severity:
        cna["metrics"] = [{"cvssV3_1": {
            "baseSeverity": severity,
            "vectorString": "CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H"}}]
    return {"cveMetadata": {"cveId": cve_id,
                            "datePublished": "2026-10-01T00:00:00.000Z"},
            "containers": {"cna": cna}}


@pytest.mark.parametrize("deb, kernel", [
    (QLI_VERSION, "7.2.0"),
    ("7.3.0~rc4+git20261007~gba86f6790835-0qli1~bpo13+1", "7.3.0-rc4"),
    ("6.12.48-1", "6.12.48"),
    ("6.12.48-1~bpo12+1", "6.12.48"),
])
def test_deb_kernel_version(deb, kernel):
    assert str(kernel_cves.deb_kernel_version(deb)) == kernel


@pytest.mark.parametrize("version, affected", [
    ("5.15", False),      # before the bug was introduced
    ("6.6.10", True),     # stable branch, before its fix
    ("6.6.79", False),    # stable fix
    ("6.6.100", False),   # after the stable fix
    ("6.12.0", True),     # branch without a stable fix
    ("6.14.0-rc3", True),  # rc of the mainline fix release counts as before
    ("6.14.0", False),    # mainline fix
    ("7.2.0", False),
])
def test_cve_affects(version, affected):
    cve = record("CVE-2026-1", semver("5.19", "6.6.79", mainline="6.14"),
                 ["io_uring/kbuf.c"])
    numbers, _, rc = version.partition("-rc")
    kernel = kernel_cves.KernelVersion(map(int, numbers.split(".")),
                                       int(rc) if rc else None)
    assert kernel_cves.cve_affects(cve, kernel) is affected


def test_cve_affects_stable_only_bug():
    # a bug only a stable backport introduced, unaffected by default
    versions = [{"version": "6.6.50", "lessThan": "6.6.55",
                 "status": "affected", "versionType": "semver"}]
    cve = record("CVE-2026-2", versions, ["mm/foo.c"], default="unaffected")
    assert kernel_cves.cve_affects(cve, kernel_cves.deb_kernel_version(
        "6.6.52-1"))
    assert not kernel_cves.cve_affects(cve, kernel_cves.deb_kernel_version(
        QLI_VERSION))


def test_cve_affects_without_mainline_fix():
    # some records only have stable fixes; then later versions are affected
    cve = record("CVE-2021-3", semver("5.10", "5.17.2"), ["drivers/x.c"])
    assert kernel_cves.cve_affects(cve, kernel_cves.deb_kernel_version(
        QLI_VERSION))


def write_inputs(tmp_path, records, version=QLI_VERSION):
    for cve in records:
        cve_id = cve["cveMetadata"]["cveId"]
        path = tmp_path / "vulns" / "cve" / "published" / \
            cve_id.split("-")[1] / f"{cve_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cve))
    debs = [{"type": "deb", "name": name, "version": version,
             "metadata": {"source": "linux-qcom-7.2"}}
            for name in ("linux-image-qcom-7.2", "linux-headers-qcom-7.2")]
    debs.append({"type": "deb", "name": "bash", "version": "5.2.37-2",
                 "metadata": {"source": "bash"}})
    sbom = tmp_path / "sbom.json"
    sbom.write_text(json.dumps({
        "artifacts": debs,
        "distro": {"id": "debian", "versionID": "13.7",
                   "versionCodename": "trixie"}}))
    return sbom


def test_run(tmp_path):
    sbom = write_inputs(tmp_path, [
        record("CVE-2026-10", semver("5.4", mainline="7.3"),
               ["net/core/dev.c"], severity="HIGH"),
        record("CVE-2026-11", semver("5.4", mainline="7.3"),
               ["drivers/gpu/drm/amd/amdgpu/amdgpu.c"]),
        record("CVE-2026-12", semver("5.4", mainline="7.1"),
               ["net/core/dev.c"]),
    ])
    result = subprocess.run(
        [sys.executable, SCRIPT, "--vulns", str(tmp_path / "vulns"),
         "--sbom", str(sbom), "--osv-dir", str(tmp_path / "osv")],
        check=True, capture_output=True, text=True)
    assert "3 kernel CVEs, 2 affect the version" in result.stdout
    assert "2 open: 1 high, 1 unknown" in result.stdout

    osv_dir = tmp_path / "osv" / f"linux-qcom-7.2_{QLI_VERSION}"
    assert sorted(os.listdir(osv_dir)) == [
        "CVE-2026-10.osv.json", "CVE-2026-11.osv.json"]
    osv = json.loads((osv_dir / "CVE-2026-10.osv.json").read_text())
    assert osv["summary"] == "foo: fix bar"
    assert osv["database_specific"] == {"severity": "high"}
    assert osv["affected"] == [{
        "package": {"ecosystem": "Debian:13", "name": "linux-qcom-7.2"},
        "versions": [QLI_VERSION],
    }]


def run_script(*args):
    return subprocess.run([sys.executable, SCRIPT, *map(str, args)],
                          check=False, capture_output=True, text=True)


@pytest.mark.parametrize("source", ["linux", "linux-signed-arm64"])
def test_run_with_debian_kernel(tmp_path, source):
    # Debian's kernel is the Debian security tracker's: nothing to report;
    # its images are built from linux-signed-<architecture>
    sbom = write_inputs(tmp_path, [
        record("CVE-2026-10", semver("5.4", mainline="7.3"),
               ["net/core/dev.c"])])
    data = json.loads(sbom.read_text())
    for artifact in data["artifacts"]:
        artifact["metadata"]["source"] = source
    sbom.write_text(json.dumps(data))
    result = run_script("--vulns", tmp_path / "vulns", "--sbom", sbom,
                        "--osv-dir", tmp_path / "osv")
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert "no kernel to assess" in result.stderr
    assert not (tmp_path / "osv").exists()


def test_needs_vulns(tmp_path):
    sbom = write_inputs(tmp_path, [])
    result = run_script("--sbom", sbom)
    assert result.returncode == 2
    assert "required: --vulns" in result.stderr


@pytest.mark.parametrize("version, affected", [
    ("7.3.0", True),
    ("7.4.0-rc1", False),  # the release candidate of the fix
    ("7.4.0-rc2", False),
    ("7.4.0", False),
])
def test_cve_affects_release_candidate_fix(version, affected):
    # a fix merged for a release candidate, which the records give as such
    cve = record("CVE-2026-50", semver("6.19", "7.3.2", mainline="7.4-rc1"),
                 ["fs/ext4/inode.c"])
    numbers, _, rc = version.partition("-rc")
    kernel = kernel_cves.KernelVersion(map(int, numbers.split(".")),
                                       int(rc) if rc else None)
    assert kernel_cves.cve_affects(cve, kernel) is affected


def test_unsupported_version(tmp_path):
    # a record with a version of a form this doesn't know counts as
    # affecting the kernel, rather than failing the scan
    versions = semver("5.4", mainline="7.3")
    versions[-1]["version"] = "7.3-foo1"
    sbom = write_inputs(tmp_path, [
        record("CVE-2026-51", versions, ["net/core/dev.c"])])
    result = run_script("--vulns", tmp_path / "vulns", "--sbom", sbom)
    assert result.returncode == 0, result.stderr
    assert "warning: CVE-2026-51: unsupported version '7.3-foo1', counted " \
        "as affecting 7.2.0" in result.stdout
    assert "1 kernel CVEs, 1 affect the version" in result.stdout


@pytest.mark.parametrize("codename, ecosystem", [
    # testing has no version in its os-release, but distro-info-data knows it
    ("forky", "Debian:14"),
    ("sid", "Debian:sid"),
])
def test_release_without_version(tmp_path, codename, ecosystem):
    sbom = write_inputs(tmp_path, [
        record("CVE-2026-10", semver("5.4", mainline="7.3"),
               ["net/core/dev.c"])])
    data = json.loads(sbom.read_text())
    data["distro"] = {"id": "debian", "versionCodename": codename}
    sbom.write_text(json.dumps(data))
    distro_info = tmp_path / "debian.csv"
    distro_info.write_text("version,codename,series,created,release,eol\n"
                           "13,Trixie,trixie,2023-06-10,2025-08-09,\n"
                           "14,Forky,forky,2025-08-09,,\n"
                           ",Sid,sid,1993-08-16,,\n")
    result = run_script("--vulns", tmp_path / "vulns", "--sbom", sbom,
                        "--osv-dir", tmp_path / "osv",
                        "--distro-info", distro_info)
    assert result.returncode == 0, result.stderr
    osv = json.loads((tmp_path / "osv" / f"linux-qcom-7.2_{QLI_VERSION}" /
                      "CVE-2026-10.osv.json").read_text())
    assert osv["affected"][0]["package"]["ecosystem"] == ecosystem
