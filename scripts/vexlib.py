# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# helpers for vex-check.py

import csv
import json
import re
from datetime import datetime

# the product all our OpenVEX statements are about; it has to be
# pkg:generic/<Syft --source-name> (see the "Generate SBOMs with Syft" step in
# debos.yml on the image-build branch). Grype uses this as the product ID.
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


# --- OSV advisories ---

# QLI-<CVE> once a CVE is assigned, QLI-<year>-<n> until then; see
# advisories/README.md
ADVISORY_ID_RE = re.compile(
    r"QLI-(?:(?P<cve>CVE-[0-9]{4}-[0-9]{4,})|[0-9]{4}-[0-9]+)")

# Debian source and binary package names, and versions, as in
# SUBCOMPONENT_RE
PACKAGE_RE = re.compile(r"[a-z0-9][a-z0-9+.-]+")
# colons only after an epoch, as dpkg wants it
VERSION_RE = re.compile(
    r"[0-9]+:[0-9](?:[A-Za-z0-9.+~:-]*[A-Za-z0-9.+~])?|"
    r"[0-9](?:[A-Za-z0-9.+~-]*[A-Za-z0-9.+~])?")

RANGE_ENDS = ("fixed", "last_affected")


def compare_versions(a, b):
    # Debian version ordering, like dpkg --compare-versions; returns -1, 0 or
    # 1. Imported here rather than at the top, so that the image builds, which
    # only check coverage, need nothing but the standard library
    # pylint: disable-next=import-outside-toplevel
    from debian.debian_support import version_compare

    result = version_compare(a, b)
    return (result > 0) - (result < 0)


def range_pairs(osv_range):
    # the affected intervals of an OSV ECOSYSTEM range, as (introduced, end
    # kind, end version) tuples; the end is (None, None) for a last interval
    # that is still open. raises ValueError for anything else than ascending
    # introduced/fixed/last_affected pairs
    if not isinstance(osv_range, dict) or \
            osv_range.get("type") != "ECOSYSTEM":
        raise ValueError("ranges must have type ECOSYSTEM")
    events = osv_range.get("events")
    if not isinstance(events, list) or not events:
        raise ValueError("events must be a non-empty array")
    pairs, previous = [], None
    for event in events:
        if not isinstance(event, dict) or len(event) != 1:
            raise ValueError("each event must contain exactly one boundary")
        kind, version = next(iter(event.items()))
        if kind not in ("introduced", *RANGE_ENDS):
            raise ValueError(f"unsupported range event {kind!r}; use "
                             "introduced, fixed or last_affected")
        if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
            raise ValueError(f"invalid Debian version {version!r}")
        if kind == "introduced":
            if pairs and pairs[-1][1] is None:
                raise ValueError("introduced must be followed by fixed or "
                                 "last_affected, except in the last pair")
            pairs.append((version, None, None))
        else:
            if not pairs or pairs[-1][1] is not None:
                raise ValueError(f"{kind} must follow an introduced event")
            pairs[-1] = (pairs[-1][0], kind, version)
        if version == "0" and kind == "introduced":
            # all versions; only valid as the very first event
            if previous is not None or len(pairs) > 1:
                raise ValueError('introduced "0" must be the first event')
            continue
        if previous is not None and \
                compare_versions(previous, version) >= 0:
            raise ValueError("events must be in ascending version order")
        previous = version
    return pairs


def debian_releases(path):
    # the Debian releases of distro-info-data (debian.csv), as the OSV
    # Debian ecosystem names them: by version, e.g. "13", or by series for
    # those without one, e.g. "sid". returns {version: series} for the former
    # and the set of the latter's series
    numbered, unnumbered = {}, set()
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("version"):
                numbered[row["version"]] = row["series"]
            else:
                unnumbered.add(row["series"])
    return numbered, unnumbered
