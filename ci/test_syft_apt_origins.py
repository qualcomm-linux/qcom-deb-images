"""Tests for listing the APT archives of the packages of an SBOM"""

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

import json
import os
import subprocess
import sys

import pytest

apt_pkg = pytest.importorskip("apt_pkg")

SCRIPT = os.path.join(os.path.dirname(__file__), os.pardir, "scripts",
                      "compliance",
                      "syft-apt-origins.py")

DEBIAN = "http://deb.debian.org/debian"
SECURITY = "http://deb.debian.org/debian-security"
QLI = "https://deb.debusine.qualcomm.com/qualcomm/qli"
SNAPSHOT = QLI + "/20261102T000000Z"
KERNEL = "7.3.0+git20261030~gae2606baf50f-0qli1~bpo13+1"


def stanza(uri, suites, enabled=True, components="main"):
    return (f"Types: deb\nURIs: {uri}\nSuites: {suites}\n"
            f"Components: {components}\n"
            + ("" if enabled else "Enabled: no\n")
            + "Signed-By:\n -----BEGIN PGP PUBLIC KEY BLOCK-----\n .\n"
            " mDMEag8p\n -----END PGP PUBLIC KEY BLOCK-----\n")


def list_prefix(uri, suite):
    return apt_pkg.uri_to_filename(f"{uri}/dists/{suite}/")


def archive_lists(lists, uri, suite, origin, packages, component="main"):
    prefix = list_prefix(uri, suite)
    (lists / f"{prefix}InRelease").write_text(
        "-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA512\n\n"
        f"Origin: {origin}\nSuite: {suite}\nCodename: {suite}\n"
        "-----BEGIN PGP SIGNATURE-----\n\niQEzBAEBCgAdFiEE\n"
        "-----END PGP SIGNATURE-----\n")
    (lists / f"{prefix}{component}_binary-arm64_Packages").write_text(
        "\n".join(f"Package: {name}\nVersion: {version}\n"
                  f"Architecture: {arch}\nDescription: x\n"
                  for name, version, arch in packages))


def deb(name, version, source=None, arch="arm64"):
    return {"type": "deb", "name": name, "version": version, "metadata": {
        "package": name, "version": version, "architecture": arch,
        "source": source or ""}}


def rootfs(tmp_path, sources, artifacts):
    root = tmp_path / "rootfs"
    (root / "etc/apt/sources.list.d").mkdir(parents=True)
    for name, text in sources.items():
        (root / "etc/apt/sources.list.d" / name).write_text(text)
    lists = root / "var/lib/apt/lists"
    lists.mkdir(parents=True)
    sbom = tmp_path / "sbom.json"
    sbom.write_text(json.dumps({"artifacts": artifacts + [
        {"type": "python", "name": "not-a-deb", "version": "1"}]}))
    return root, lists, sbom


def run(sbom, root):
    return subprocess.run([sys.executable, SCRIPT, str(sbom), str(root)],
                          check=False, capture_output=True, text=True)


def test_origins(tmp_path):
    root, lists, sbom = rootfs(tmp_path, {
        "debian.sources": stanza(DEBIAN, "trixie trixie-updates") + "\n"
        + stanza(SECURITY, "trixie-security"),
        "qli.sources": stanza(QLI, "trixie"),
    }, [
        deb("linux-image-qcom-next", KERNEL, "linux-qcom-next"),
        deb("bsdextrautils", "2.41.5-0+deb13u1", "util-linux"),
        deb("tzdata", "2026f-0+deb13u1", arch="all"),
        # none of the archives
        deb("linux-image-7.3.0-rc4", "7.3.0~rc4-1", "linux-upstream"),
    ])
    archive_lists(lists, DEBIAN, "trixie", "Debian",
                  [("bsdextrautils", "2.41.5-0+deb13u1", "arm64")])
    archive_lists(lists, SECURITY, "trixie-security", "Debian",
                  [("bsdextrautils", "2.41.5-0+deb13u1", "arm64"),
                   ("bsdextrautils", "2.41.4-0+deb13u1", "arm64")])
    archive_lists(lists, DEBIAN, "trixie-updates", "Debian",
                  [("tzdata", "2026f-0+deb13u1", "all")])
    archive_lists(lists, QLI, "trixie", "Qualcomm",
                  [("linux-image-qcom-next", KERNEL, "arm64")])
    result = run(sbom, root)
    assert result.returncode == 0, result.stderr
    rows = {row["package"]: row
            for row in json.loads(result.stdout)["packages"]}
    assert sorted(rows) == ["bsdextrautils", "linux-image-7.3.0-rc4",
                            "linux-image-qcom-next", "tzdata"]
    assert rows["linux-image-qcom-next"] == {
        "package": "linux-image-qcom-next", "version": KERNEL,
        "architecture": "arm64", "source": "linux-qcom-next",
        "source_version": KERNEL,
        "archives": [{"vendor": "Qualcomm", "uri": QLI, "suite": "trixie",
                      "component": "main"}],
    }
    assert [(a["uri"], a["suite"])
            for a in rows["bsdextrautils"]["archives"]] == [
        (DEBIAN, "trixie"), (SECURITY, "trixie-security")]
    assert rows["tzdata"]["source"] == "tzdata"
    assert rows["tzdata"]["archives"][0]["suite"] == "trixie-updates"
    assert rows["linux-image-7.3.0-rc4"]["archives"] == []


def test_sources(tmp_path):
    # the deb stanzas, enabled or not, as apt reads them, but local ones, and
    # the keyring files they name, through the links of the root filesystem,
    # not the host's
    keyring = "/usr/share/keyrings/debian-archive-keyring.gpg"
    debian = f"Types: deb\nURIs: {DEBIAN}\nSuites: trixie\n" \
        f"Components: main\nSigned-By: {keyring}\n"
    root, lists, sbom = rootfs(tmp_path, {
        "debian.sources": debian,
        "qli.sources": stanza(QLI + "/", "trixie", enabled=False),
        "local.sources": "Types: deb\nURIs: file:///media/local\nSuites: /\n",
    }, [deb("bash", "5.2.37-2")])
    archive_lists(lists, DEBIAN, "trixie", "Debian",
                  [("bash", "5.2.37-2", "arm64")])
    (root / "usr/share/keyrings").mkdir(parents=True)
    (root / "usr/share/keyrings/debian-archive-keyring.pgp").write_bytes(
        b"\x99key")
    (root / keyring[1:]).symlink_to(
        "/usr/share/keyrings/debian-archive-keyring.pgp")
    result = run(sbom, root)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["sources"] == {
        DEBIAN: debian, QLI: stanza(QLI + "/", "trixie", enabled=False)}
    assert output["keyrings"] == {keyring: "mWtleQ=="}


def test_snapshot_origins(tmp_path):
    # the image recipe removes the snapshot sources of snapshot builds after
    # their last APT update: their lists are those the packages come from
    debian_snapshot = "https://snapshot.debian.org/archive/debian/" \
        "20261102T000000Z"
    root, lists, sbom = rootfs(tmp_path, {
        "debian.sources": stanza(DEBIAN, "trixie"),
        "qli.sources": stanza(QLI, "trixie"),
    }, [deb("linux-image-qcom-next", KERNEL, "linux-qcom-next"),
        deb("bash", "5.2.37-2")])
    archive_lists(lists, SNAPSHOT, "trixie", "Qualcomm",
                  [("linux-image-qcom-next", KERNEL, "arm64")])
    archive_lists(lists, debian_snapshot, "trixie", "Debian",
                  [("bash", "5.2.37-2", "arm64")])
    result = run(sbom, root)
    assert result.returncode == 0, result.stderr
    rows = {row["package"]: row
            for row in json.loads(result.stdout)["packages"]}
    assert rows["linux-image-qcom-next"]["archives"] == [{
        "vendor": "Qualcomm", "uri": SNAPSHOT, "suite": "trixie",
        "component": "main"}]
    assert rows["bash"]["archives"] == [{
        "vendor": "Debian", "uri": debian_snapshot, "suite": "trixie",
        "component": "main"}]


def test_uri_of_source(tmp_path):
    # the scheme of the URI is that of its source, enabled or not, without
    # credentials
    root, lists, sbom = rootfs(tmp_path, {
        "qli.sources": stanza("http://user:pw@deb.debusine.qualcomm.com/"
                              "qualcomm/qli/", "trixie", enabled=False)},
        [deb("linux-image-qcom-next", KERNEL, "linux-qcom-next")])
    archive_lists(lists, "http://deb.debusine.qualcomm.com/qualcomm/qli",
                  "trixie", "Qualcomm",
                  [("linux-image-qcom-next", KERNEL, "arm64")])
    result = run(sbom, root)
    assert result.returncode == 0, result.stderr
    row, = json.loads(result.stdout)["packages"]
    assert row["archives"][0]["uri"] == \
        "http://deb.debusine.qualcomm.com/qualcomm/qli"


def test_unknown_archive_without_source(tmp_path):
    # e.g. of a source removed since: an error, like with a source
    root, lists, sbom = rootfs(tmp_path, {
        "qli.sources": stanza(QLI, "trixie")},
        [deb("linux-image-qcom-next", KERNEL, "linux-qcom-next")])
    archive_lists(lists, QLI, "trixie", "Qualcomm",
                  [("linux-image-qcom-next", KERNEL, "arm64")])
    archive_lists(lists, "http://other.example/debian", "trixie", "Other",
                  [("linux-image-qcom-next", KERNEL, "arm64")])
    result = run(sbom, root)
    assert result.returncode == 1
    assert "unknown archive host of https://other.example/debian" in \
        result.stderr


def test_flat_repository_list(tmp_path):
    # e.g. of the local APT repository, whose lists the image recipe removes
    # though: skipped, with a warning
    root, lists, sbom = rootfs(tmp_path, {
        "qli.sources": stanza(QLI, "trixie")},
        [deb("linux-image-qcom-next", KERNEL, "linux-qcom-next")])
    archive_lists(lists, QLI, "trixie", "Qualcomm",
                  [("linux-image-qcom-next", KERNEL, "arm64")])
    (lists / "_media_apt-local-repo_._Packages").write_text(
        f"Package: linux-image-qcom-next\nVersion: {KERNEL}\n"
        "Architecture: arm64\n")
    result = run(sbom, root)
    assert result.returncode == 0, result.stderr
    assert "_media_apt-local-repo_._Packages: not the list of an archive " \
        "with suites and components, skipped" in result.stderr
    row, = json.loads(result.stdout)["packages"]
    assert [a["uri"] for a in row["archives"]] == [QLI]


@pytest.mark.parametrize("uri, origin, message", [
    ("http://other.example/debian", "Debian",
     "unknown archive host of http://other.example/debian"),
    (QLI, "Debian", "Origin 'Debian' doesn't match the vendor 'Qualcomm'"),
    (DEBIAN, "", "Origin '' doesn't match the vendor 'Debian'"),
])
def test_unknown_archives(tmp_path, uri, origin, message):
    root, lists, sbom = rootfs(tmp_path, {"x.sources": stanza(uri, "trixie")},
                               [deb("bash", "5.2.37-2")])
    archive_lists(lists, uri, "trixie", origin,
                  [("bash", "5.2.37-2", "arm64")])
    result = run(sbom, root)
    assert result.returncode == 1
    assert message in result.stderr
    assert result.stdout == ""


def test_no_release_file(tmp_path):
    root, lists, sbom = rootfs(tmp_path, {"q.sources": stanza(QLI, "trixie")},
                               [deb("bash", "5.2.37-2")])
    archive_lists(lists, QLI, "trixie", "Qualcomm",
                  [("bash", "5.2.37-2", "arm64")])
    os.remove(lists / (list_prefix(QLI, "trixie") + "InRelease"))
    result = run(sbom, root)
    assert result.returncode == 1
    assert "no Release file" in result.stderr


def test_no_package_found(tmp_path):
    # the APT lists are missing, or don't match the SBOM
    root, _, sbom = rootfs(tmp_path, {"q.sources": stanza(QLI, "trixie")},
                           [deb("bash", "5.2.37-2")])
    result = run(sbom, root)
    assert result.returncode == 1
    assert "no package found in the APT lists" in result.stderr
    assert result.stdout == ""
