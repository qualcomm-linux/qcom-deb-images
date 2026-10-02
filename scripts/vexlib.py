# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# helpers shared by vex-check.py and grype-vulnerability-summary.py

import json
import re
from datetime import datetime

# the product all our OpenVEX statements are about; it has to be
# pkg:generic/<Syft --source-name> (see the "Generate SBOMs with Syft" step in
# .github/workflows/debos.yml), which is how Grype identifies the product
PRODUCT = "pkg:generic/qualcomm-linux-debian-rootfs"

OPENVEX_CONTEXT = "https://openvex.dev/ns/v0.2.0"


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_time(value):
    if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?"
            r"(?:[Zz]|[+-]\d{2}:\d{2})", value):
        return None
    try:
        return datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
    except ValueError:
        return None


# --- package URLs ---

# the only subcomponent form we accept: a Debian binary package name, with an
# optional version, as Debian writes them. Grype (via go-vex and
# packageurl-go) matches these against the SBOM's percent-encoded purls with
# qualifiers, so no qualifiers, subpath or percent-encoding are needed, and
# without them comparing names and versions is enough to find overlaps
SUBCOMPONENT_RE = re.compile(
    r"pkg:deb/debian/(?P<name>[a-z0-9][a-z0-9+.-]+)"
    r"(?:@(?P<version>(?:[0-9]+:)?[0-9][A-Za-z0-9.+~:-]*))?")


def parse_subcomponent(purl):
    # returns (name, version or None), or None when purl isn't in that form
    match = SUBCOMPONENT_RE.fullmatch(purl) if isinstance(purl, str) \
        else None
    return (match["name"], match["version"]) if match else None


def statement_vulnerability_ids(statement):
    vuln = statement.get("vulnerability") or {}
    ids = {vuln.get("name")}
    ids.update(vuln.get("aliases") or [])
    ids.discard(None)
    return ids


def statement_subcomponents(statement):
    # yields the subcomponent purls of every product, which vex-check makes
    # sure is ours
    for product in statement.get("products") or []:
        for sub in product.get("subcomponents") or []:
            yield sub.get("@id")


# --- Syft SBOM ---

def sbom_debs(sbom):
    # yields the installed Debian binary packages, with their source package
    for artifact in sbom.get("artifacts") or []:
        if artifact.get("type") != "deb":
            continue
        metadata = artifact.get("metadata") or {}
        name, version = artifact.get("name"), artifact.get("version")
        source = metadata.get("source") or name
        yield {"name": name, "version": version, "source": source}
