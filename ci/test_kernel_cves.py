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
# for kernellib, which the script imports
sys.path.insert(0, SCRIPTS)

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


@pytest.mark.parametrize("files, present", [
    (["net/core/dev.c", "drivers/x.c"], ["net/core/dev.c"]),
    (["Documentation/foo.rst", "drivers/x.c"], []),
    # files other than code may be compiled, e.g. device trees
    (["arch/arm64/boot/dts/qcom/x.dtsi"], ["arch/arm64/boot/dts/qcom/x.dtsi"]),
    # build files don't matter along with code
    (["drivers/hid/Kconfig", "drivers/hid/hid-rapoo.c"], []),
    # else only those of directories with compiled sources
    (["net/core/Kconfig"], ["net/core/Kconfig"]),
    (["arch/riscv/Kconfig.errata"], []),
])
def test_compiled(files, present):
    sources = kernel_cves.CompiledSources(
        ["net/core/dev.c", "include/linux/skbuff.h"])
    assert sources.present(files) == present


def write_inputs(tmp_path, records, version=QLI_VERSION, vendor="Qualcomm",
                 image="linux-image-qcom-7.2", source="linux-qcom-7.2",
                 image_path="/boot/vmlinuz-7.2.0+qcom-7.2"):
    # the CVE records, an SBOM with a kernel, image installing image_path,
    # and the APT origins of its packages, all from the
    # archives of vendor, or none
    for cve in records:
        cve_id = cve["cveMetadata"]["cveId"]
        path = tmp_path / "vulns" / "cve" / "published" / \
            cve_id.split("-")[1] / f"{cve_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cve))
    debs = [{"id": name, "type": "deb", "name": name, "version": version,
             "metadata": {"source": source}}
            for name in (image, "linux-headers-qcom-7.2") if name]
    debs.append({"id": "bash", "type": "deb", "name": "bash",
                 "version": "5.2.37-2", "metadata": {"source": "bash"}})
    files = [{"id": "vmlinuz", "location": {"path": image_path}},
             {"id": "sh", "location": {"path": "/usr/bin/bash"}}]
    relationships = [{"parent": "bash", "child": "sh", "type": "contains"}]
    if image:
        relationships.append({"parent": image, "child": "vmlinuz",
                              "type": "contains"})
    sbom = tmp_path / "sbom.json"
    sbom.write_text(json.dumps({
        "artifacts": debs, "files": files,
        "artifactRelationships": relationships,
        "distro": {"id": "debian", "versionID": "13.7",
                   "versionCodename": "trixie"}}))
    archive = {"vendor": vendor, "uri": "https://example.com/" + str(vendor),
               "suite": "trixie", "component": "main"}
    (tmp_path / "origins.json").write_text(json.dumps({"packages": [
        {"package": deb["name"], "version": deb["version"],
         "architecture": "arm64", "source": deb["metadata"]["source"],
         "source_version": deb["version"],
         "archives": [archive] if vendor else []} for deb in debs]}))
    return sbom


def write_sources(tmp_path):
    # the compiled sources of the kernel, as scripts/kernel-compiled-sources.py
    # names them
    sources = tmp_path / "compiled-sources"
    sources.mkdir()
    (sources / f"linux-qcom-7.2_{QLI_VERSION}.compiled-sources.txt") \
        .write_text("net/core/dev.c\ninclude/linux/skbuff.h\n")
    return sources


def test_run(tmp_path):
    sources = write_sources(tmp_path)
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
         "--sbom", str(sbom), "--apt-origins", str(tmp_path / "origins.json"),
         "--compiled-sources", str(sources),
         "--osv-dir", str(tmp_path / "osv"),
         "--vex", str(tmp_path / "kernel.openvex.json")],
        check=True, capture_output=True, text=True)
    assert "3 kernel CVEs, 2 affect the version" in result.stdout
    assert "1 of them change no compiled file" in result.stdout
    assert "1 open: 1 high" in result.stdout

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

    vex = json.loads((tmp_path / "kernel.openvex.json").read_text())
    statement, = vex["statements"]
    assert statement["vulnerability"] == {"name": "CVE-2026-11"}
    assert statement["status"] == "not_affected"
    assert statement["justification"] == "vulnerable_code_not_present"
    assert statement["products"] == [{
        "@id": "pkg:generic/qualcomm-linux-debian-rootfs",
        "subcomponents": [
            {"@id": f"pkg:deb/debian/linux-headers-qcom-7.2@{QLI_VERSION}"},
            {"@id": f"pkg:deb/debian/linux-image-qcom-7.2@{QLI_VERSION}"},
        ]}]


def run_script(*args):
    return subprocess.run([sys.executable, SCRIPT, *map(str, args)],
                          check=False, capture_output=True, text=True)


def run_kernel_cves(tmp_path, sbom, *args):
    return run_script("--vulns", tmp_path / "vulns", "--sbom", sbom,
                      "--apt-origins", tmp_path / "origins.json",
                      "--osv-dir", tmp_path / "osv", *args)


CVE_10 = record("CVE-2026-10", semver("5.4", mainline="7.3"),
                ["net/core/dev.c"])


def test_run_with_debian_kernel(tmp_path):
    # a kernel from a Debian archive is the Debian security tracker's:
    # nothing to report, whatever its source package is called, e.g.
    # linux-signed-arm64
    sbom = write_inputs(tmp_path, [CVE_10], vendor="Debian",
                        image="linux-binary-7.2.12+deb13-arm64",
                        source="linux-signed-arm64",
                        image_path="/usr/lib/modules/7.2.12+deb13-arm64/"
                        "vmlinuz")
    result = run_kernel_cves(tmp_path, sbom)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert "no non-Debian kernel to assess" in result.stderr
    assert not (tmp_path / "osv").exists()


def test_run_without_kernel(tmp_path):
    # an image without kernel is an error, rather than nothing to report
    sbom = write_inputs(tmp_path, [CVE_10], image=None)
    result = run_kernel_cves(tmp_path, sbom)
    assert result.returncode == 1
    assert "no package installs /boot/vmlinuz-* or " \
        "/usr/lib/modules/*/vmlinuz" in result.stderr


def test_not_a_kernel_image(tmp_path):
    # e.g. a helper that reads kernel images, of needrestart
    sbom = write_inputs(tmp_path, [CVE_10],
                        image_path="/usr/lib/needrestart/vmlinuz-get-version")
    result = run_kernel_cves(tmp_path, sbom)
    assert result.returncode == 1
    assert "no package installs" in result.stderr


@pytest.mark.parametrize("image_path", [
    "/boot/vmlinuz-7.2.0+qcom-7.2",
    # like Debian's current kernels
    "/usr/lib/modules/7.2.0+qcom-7.2/vmlinuz",
    "/lib/modules/7.2.0+qcom-7.2/vmlinuz",
])
def test_kernel_by_its_files(tmp_path, image_path):
    # the kernel is the package installing a kernel image, whatever its name
    sbom = write_inputs(tmp_path, [CVE_10], image="qcom-kernel-blob",
                        source="kernel-qcom", image_path=image_path)
    result = run_kernel_cves(tmp_path, sbom)
    assert result.returncode == 0, result.stderr
    assert f"kernel-qcom {QLI_VERSION} (kernel 7.2.0, " \
        "linux-headers-qcom-7.2, qcom-kernel-blob)" in result.stdout
    assert (tmp_path / "osv" / f"kernel-qcom_{QLI_VERSION}" /
            "CVE-2026-10.osv.json").exists()


def test_kernel_from_no_archive(tmp_path):
    # e.g. a bindeb-pkg build from a local APT repository
    sbom = write_inputs(tmp_path, [CVE_10], vendor=None)
    result = run_kernel_cves(tmp_path, sbom)
    assert result.returncode == 0, result.stderr
    assert f"warning: linux-qcom-7.2 {QLI_VERSION}: from no known APT " \
        "archive, assessing it like a QLI kernel" in result.stdout
    assert "1 kernel CVEs, 1 affect the version" in result.stdout


def test_run_without_apt_origins(tmp_path):
    # e.g. the SBOM of an image built before they were recorded
    sbom = write_inputs(tmp_path, [CVE_10])
    (tmp_path / "origins.json").unlink()
    result = run_kernel_cves(tmp_path, sbom)
    assert result.returncode == 0, result.stderr
    assert "warning: no APT origins of the image" in result.stdout
    assert not (tmp_path / "osv").exists()


def test_unknown_product(tmp_path):
    # rather than silently skipping its CVEs
    cve = record("CVE-2026-11", semver("5.4", mainline="7.3"),
                 ["net/core/dev.c"])
    cve["containers"]["cna"]["affected"][0]["product"] = "linux"
    sbom = write_inputs(tmp_path, [CVE_10, cve])
    result = run_kernel_cves(tmp_path, sbom)
    assert result.returncode == 1
    assert "CVE-2026-11.json: unknown product(s) linux" in result.stderr


@pytest.mark.parametrize("deb", [
    "", "foo", "v7.3", "7.3rc4-1", "-1", "1:", "7..3-1",
])
def test_deb_kernel_version_invalid(deb):
    with pytest.raises(ValueError, match="not a kernel package version"):
        kernel_cves.deb_kernel_version(deb)


def test_run_with_invalid_kernel_version(tmp_path):
    # an error, as the kernel's CVEs can't be assessed
    sbom = write_inputs(tmp_path, [CVE_10], version="foo")
    result = run_kernel_cves(tmp_path, sbom)
    assert result.returncode == 1
    assert "not a kernel package version: foo" in result.stderr


@pytest.mark.parametrize("value", ["", "x", "7.3-rc", "7.3-foo1", "7.3.*"])
def test_cve_version_invalid(value):
    with pytest.raises(ValueError, match="unsupported version"):
        kernel_cves.cve_version(value)


def test_compiled_sources_missing(tmp_path):
    # without a list, e.g. without -dbg package, by version only, and no VEX
    sbom = write_inputs(tmp_path, [
        record("CVE-2026-11", semver("5.4", mainline="7.3"),
               ["drivers/gpu/drm/amd/amdgpu/amdgpu.c"])])
    lists = tmp_path / "compiled-sources"
    lists.mkdir()
    vex = tmp_path / "kernel.openvex.json"
    result = run_kernel_cves(tmp_path, sbom, "--compiled-sources", lists,
                             "--vex", vex)
    assert result.returncode == 0, result.stderr
    assert f"warning: no compiled sources for linux-qcom-7.2 {QLI_VERSION}" \
        in result.stdout
    assert "1 open: 1 unknown" in result.stdout
    assert not vex.exists()


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
    result = run_script("--vulns", tmp_path / "vulns", "--sbom", sbom,
                        "--apt-origins", tmp_path / "origins.json")
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
    result = run_kernel_cves(tmp_path, sbom)
    assert result.returncode == 0, result.stderr
    osv = json.loads((tmp_path / "osv" / f"linux-qcom-7.2_{QLI_VERSION}" /
                      "CVE-2026-10.osv.json").read_text())
    assert osv["affected"][0]["package"]["ecosystem"] == ecosystem
