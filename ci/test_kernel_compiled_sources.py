"""Tests for the compiled kernel sources lists"""

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import subprocess
import sys

import pytest

TOP = os.path.join(os.path.dirname(__file__), os.pardir)
SCRIPT = os.path.join(TOP, "scripts", "kernel-compiled-sources.py")
# for kernellib, which the script imports
sys.path.insert(0, os.path.join(TOP, "scripts"))

spec = importlib.util.spec_from_file_location("kernel_compiled_sources",
                                              SCRIPT)
kcs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kcs)

BUILD = "/build/reproducible-path/linux-qcom-next-7.3.0"
QLI = "https://deb.debusine.qualcomm.com/qualcomm/qli"
VERSION = "7.3.0+git20261030~gae2606baf50f-0qli1~bpo13+1"
IMAGE = "linux-image-7.3.0+qcom-next-20261030-gae2606baf50f"
KEY = ("\n -----BEGIN PGP PUBLIC KEY BLOCK-----\n .\n mDMEag8p\n"
       " -----END PGP PUBLIC KEY BLOCK-----")


def test_compiled_sources(tmp_path, monkeypatch):
    # the files debugedit lists under the source trees: the compilation
    # directory of vmlinux, as readelf prints it, and the given ones
    for name in ("vmlinux-7.3.0", "a.ko"):
        (tmp_path / name).write_text("")
    debug_info = [f"{BUILD}/init/main.c", f"{BUILD}/include/linux/init.h",
                  f"{BUILD}/<built-in>", "/usr/lib/gcc/include/stddef.h",
                  "/other/tree/drivers/x.c"]
    runs = []

    def run(command, **_):
        runs.append(command)
        if command[0] == "readelf":
            return subprocess.CompletedProcess(command, 0, stdout=(
                "    <17>   DW_AT_comp_dir    : (indirect line string, "
                f"offset: 0): {BUILD}\n"))
        base = command[command.index("--base-dir") + 1] + "/"
        with open(command[command.index("--list-file") + 1], "wb") as f:
            f.write(b"".join(name[len(base):].encode() + b"\0"
                             for name in debug_info if name.startswith(base)))
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(kcs.subprocess, "run", run)
    debug_files, files = kcs.compiled_sources([str(tmp_path)],
                                              ["/other/tree"], 1)
    assert len(debug_files) == 2
    assert files == {"init/main.c", "include/linux/init.h", "drivers/x.c"}
    assert [command[-1] for command in runs if command[0] == "readelf"] == \
        [str(tmp_path / "vmlinux-7.3.0")]


def source(uri, key=KEY):
    return f"Types: deb\nURIs: {uri}\nSuites: trixie\nComponents: main\n" \
        f"Signed-By:{key}\n"


def archive(uri, vendor="Qualcomm"):
    return {"vendor": vendor, "uri": uri, "suite": "trixie",
            "component": "main"}


@pytest.mark.parametrize("uri, stanza", [
    (QLI, source(QLI)),
    # a snapshot of the QLI repository has the key of its source
    (QLI + "/20261102T000000Z", source(QLI + "/20261102T000000Z")),
    (QLI + "-other", None),
])
def test_archive_source(tmp_path, uri, stanza):
    assert kcs.archive_source(archive(uri), {"sources": {QLI: source(QLI)}},
                              str(tmp_path)) == stanza


def test_archive_source_keyring_files(tmp_path):
    # naming the keyring of the image, and the key to take from it
    fingerprint = "A7236886F3CCCAAD148A27F80E98404D386FA1D9"
    keyring = "/usr/share/keyrings/qli.gpg"
    stanza = kcs.archive_source(archive(QLI), {
        "sources": {QLI: source(QLI, f" {keyring}, {fingerprint}")},
        "keyrings": {keyring: base64.b64encode(b"\x99key").decode()},
    }, str(tmp_path))
    path = tmp_path / (hashlib.sha256(b"\x99key").hexdigest() + ".gpg")
    assert stanza == source(QLI, f" {path} {fingerprint}")
    assert path.read_bytes() == b"\x99key"


@pytest.mark.parametrize("key", [
    # the keyring of the image, but not recorded
    " /usr/share/keyrings/missing.gpg",
    # only a fingerprint, of any keyring apt has configured
    " A7236886F3CCCAAD148A27F80E98404D386FA1D9",
])
def test_archive_source_without_key(tmp_path, key):
    assert kcs.archive_source(archive(QLI), {"sources": {
        QLI: source(QLI, key)}}, str(tmp_path)) is None


def write_inputs(tmp_path, kernels):
    # an SBOM with kernels, (image package, source, archives) each, and
    # their APT origins
    artifacts, files, relationships, rows = [], [], [], []
    for image, source_package, archives in kernels:
        artifacts.append({"id": image, "type": "deb", "name": image,
                          "version": VERSION,
                          "metadata": {"source": source_package}})
        files.append({"id": f"{image}-vmlinuz",
                      "location": {"path": f"/boot/vmlinuz-{image}"}})
        relationships.append({"parent": image, "child": f"{image}-vmlinuz",
                              "type": "contains"})
        rows.append({"package": image, "version": VERSION,
                     "architecture": "arm64", "archives": archives})
    sbom = tmp_path / "sbom.json"
    sbom.write_text(json.dumps({"artifacts": artifacts, "files": files,
                                "artifactRelationships": relationships}))
    origins = tmp_path / "origins.json"
    origins.write_text(json.dumps({"packages": rows,
                                   "sources": {QLI: source(QLI)}}))
    return argparse.Namespace(sbom=str(sbom), apt_origins=str(origins),
                              output=str(tmp_path / "out"), prefix=[],
                              jobs=1)


def test_sbom_kernels(tmp_path):
    args = write_inputs(tmp_path, [
        (IMAGE, "linux-qcom-next", [archive(QLI)]),
        # Debian's kernel is the Debian security tracker's
        ("linux-binary-7.2.12+deb13-arm64", "linux-signed-arm64",
         [archive("http://deb.debian.org/debian", "Debian")]),
        # e.g. a bindeb-pkg build from a local APT repository
        ("linux-image-7.2.0-local", "linux-upstream", []),
    ])
    kernels, _ = kcs.sbom_kernels(args)
    assert kernels == {
        ("linux-qcom-next", VERSION): ("arm64", [IMAGE], [archive(QLI)]),
        ("linux-upstream", VERSION): ("arm64", ["linux-image-7.2.0-local"],
                                      []),
    }


def test_download_fails(tmp_path, monkeypatch, capsys):
    # e.g. the archive is unreachable: by version only, with a warning
    args = write_inputs(tmp_path, [(IMAGE, "linux-qcom-next",
                                    [archive(QLI)])])

    def apt_cache(_, stanza, architecture):
        assert (stanza, architecture) == (source(QLI), "arm64")
        raise SystemError("E:Failed to fetch")
    monkeypatch.setattr(kcs, "apt_cache", apt_cache)
    assert kcs.fetch(args) == 0
    assert "cannot list the compiled sources: E:Failed to fetch" in \
        capsys.readouterr().out
    assert not os.listdir(args.output)


def test_without_apt_origins(tmp_path):
    # e.g. the SBOM of an image built before they were: by version only
    args = write_inputs(tmp_path, [(IMAGE, "linux-qcom-next",
                                    [archive(QLI)])])
    result = subprocess.run(
        [sys.executable, SCRIPT, "--sbom", args.sbom, "--apt-origins",
         str(tmp_path / "none"), "--output", args.output],
        check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "warning: no APT origins of the image" in result.stdout
