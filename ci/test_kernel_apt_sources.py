"""Tests for recording the APT source of an image's kernel"""

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

import importlib.util
import os

SCRIPT = os.path.join(os.path.dirname(__file__), os.pardir, "scripts",
                      "kernel-apt-sources.py")

spec = importlib.util.spec_from_file_location("kernel_apt_sources", SCRIPT)
kas = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kas)

QLI = """Types: deb
URIs: https://deb.debusine.qualcomm.com/qualcomm/qli
Suites: trixie
Components: main
Enabled: yes
Signed-By:
 -----BEGIN PGP PUBLIC KEY BLOCK-----
 .
 mDMEag8p/xYJKwYBBAHaRw8BAQdAdB6JSNF1OXxnsTgp4VTUekW52BM7e6ZQVRsq
 -----END PGP PUBLIC KEY BLOCK-----
"""

SNAPSHOT = """# a snapshot of the QLI repository
Types: deb
URIs: https://deb.debusine.qualcomm.com/qualcomm/qli/20261102T000000Z/
Suites: trixie
Components: main
Check-Valid-Until: no
"""


def rootfs(tmp_path, sources, status=""):
    directory = tmp_path / "etc" / "apt" / "sources.list.d"
    directory.mkdir(parents=True)
    for name, text in sources.items():
        (directory / name).write_text(text)
    dpkg = tmp_path / "var" / "lib" / "dpkg"
    dpkg.mkdir(parents=True)
    (dpkg / "status").write_text(status)
    return str(tmp_path)


def test_paragraphs():
    # the Signed-By key inline is one field, and the stanzas stay as they are
    (stanza, fields), = kas.paragraphs(QLI)
    assert stanza == QLI
    assert fields["uris"] == "https://deb.debusine.qualcomm.com/qualcomm/qli"
    assert fields["signed-by"].splitlines()[1:3] == [
        " -----BEGIN PGP PUBLIC KEY BLOCK-----", " ."]


def test_image_sources(tmp_path):
    # enabled deb sources only, but local ones
    disabled = QLI.replace("Enabled: yes", "Enabled: no")
    path = rootfs(tmp_path, {
        "qli.sources": disabled,
        "snapshot_qli.sources": SNAPSHOT,
        "local.sources": "Types: deb\nURIs: file:/media/apt-local-repo\n"
                         "Suites: local\n\nTypes: deb-src\n"
                         "URIs: http://deb.debian.org/debian\n"
                         "Suites: trixie\n",
        "debian.list": "deb http://deb.debian.org/debian trixie main\n",
    })
    assert [stanza for stanza, _ in kas.image_sources(path)] == [SNAPSHOT]


def test_provides():
    (_, fields), = kas.paragraphs(SNAPSHOT)
    snapshot = "https://deb.debusine.qualcomm.com/qualcomm/qli/" \
        "20261102T000000Z"
    assert kas.provides(fields, snapshot, "trixie")
    assert kas.provides(fields, snapshot + "/", "trixie")
    assert not kas.provides(fields, snapshot, "forky")
    assert not kas.provides(
        fields, "https://deb.debusine.qualcomm.com/qualcomm/qli", "trixie")


def test_installed_kernels(tmp_path):
    # Debian's kernel is the Debian security tracker's, its images built
    # from linux-signed-<architecture>
    path = rootfs(tmp_path, {}, status="""Package: linux-image-qcom-next
Status: install ok installed
Architecture: arm64
Source: linux-qcom-next (7.3.0+git20261030~gae2606baf50f-0qli1~bpo13+1)
Version: 7.3.0+git20261030~gae2606baf50f-0qli1~bpo13+1

Package: linux-image-arm64
Status: install ok installed
Architecture: arm64
Source: linux-signed-arm64
Version: 6.12.48-1

Package: linux-image-6.12.48-arm64
Status: install ok installed
Architecture: arm64
Source: linux
Version: 6.12.48-1

Package: linux-image-old
Status: deinstall ok config-files
Architecture: arm64
Source: linux-qcom-next
Version: 7.2.0-1
""")
    assert list(kas.installed_kernels(path)) == [
        ("linux-image-qcom-next",
         "7.3.0+git20261030~gae2606baf50f-0qli1~bpo13+1", "arm64"),
    ]
