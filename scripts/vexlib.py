# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# helpers shared by vex-check.py, vex-merge.py and
# grype-vulnerability-summary.py

import json
from urllib.parse import unquote

# the product all our OpenVEX statements are about; it has to be
# pkg:generic/<Syft --source-name> (see the "Generate SBOMs with Syft" step in
# .github/workflows/debos.yml), which is how Grype identifies the product
PRODUCT = "pkg:generic/qualcomm-linux-debian-rootfs"

OPENVEX_CONTEXT = "https://openvex.dev/ns/v0.2.0"

VEX_STATUSES = ("not_affected", "affected", "fixed", "under_investigation")

# statuses that hide a finding; the others only describe it
SUPPRESSING = ("not_affected", "fixed")

JUSTIFICATIONS = (
    "component_not_present",
    "vulnerable_code_not_present",
    "vulnerable_code_not_in_execute_path",
    "vulnerable_code_cannot_be_controlled_by_adversary",
    "inline_mitigations_already_exist",
)


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


# --- package URLs ---

class Purl:
    # only what we need of https://github.com/package-url/purl-spec
    def __init__(self, text):
        self.text = text
        if not isinstance(text, str) or not text.startswith("pkg:"):
            raise ValueError(f"not a package URL: {text!r}")
        rest, _, qualifiers = text[4:].partition("?")
        rest = rest.split("#", 1)[0]
        rest, _, version = rest.partition("@")
        parts = [unquote(p) for p in rest.split("/")]
        self.type = parts[0]
        self.name = parts[-1] if len(parts) > 1 else ""
        self.namespace = "/".join(parts[1:-1])
        self.version = unquote(version)
        self.qualifiers = {}
        for item in qualifiers.split("&") if qualifiers else []:
            key, _, value = item.partition("=")
            self.qualifiers[key] = unquote(value)


def parse_purl(text):
    try:
        return Purl(text)
    except ValueError:
        return None


def purl_matches(wanted, actual):
    # go-vex semantics, which Grype relies on: type, namespace and name must be
    # equal; a wanted version must match exactly and no version matches all;
    # every wanted qualifier must be present and equal in the actual purl
    w, a = parse_purl(wanted), parse_purl(actual)
    if not w or not a:
        return wanted == actual
    if (w.type, w.namespace, w.name) != (a.type, a.namespace, a.name):
        return False
    if w.version and w.version != a.version:
        return False
    return all(a.qualifiers.get(k) == v for k, v in w.qualifiers.items())


def purls_overlap(first, second):
    # Two scopes overlap unless their identities, pinned versions or shared
    # qualifiers conflict. Neither scope has to contain the other.
    a, b = parse_purl(first), parse_purl(second)
    if not a or not b:
        return first == second
    if (a.type, a.namespace, a.name) != (b.type, b.namespace, b.name):
        return False
    if a.version and b.version and a.version != b.version:
        return False
    return all(a.qualifiers[key] == b.qualifiers[key]
               for key in a.qualifiers.keys() & b.qualifiers.keys())


def statement_vulnerability_ids(statement):
    vuln = statement.get("vulnerability") or {}
    ids = {vuln.get("name")}
    ids.update(vuln.get("aliases") or [])
    ids.discard(None)
    return ids


def statement_purls(statement):
    # yields (product purl, subcomponent purl) for every combination
    for product in statement.get("products") or []:
        subs = [s.get("@id") for s in product.get("subcomponents") or []]
        for sub in subs:
            yield product.get("@id"), sub


def find_statement(documents, vuln_ids, product_purl, subcomponent_purl):
    # returns the statement covering the vulnerability, product and package.
    # like Grype, a suppressing statement wins over any other one, whatever
    # the order
    found = None
    for doc in documents:
        for statement in doc.get("statements") or []:
            if not statement_vulnerability_ids(statement) & set(vuln_ids):
                continue
            for product, sub in statement_purls(statement):
                if not (purl_matches(product, product_purl) and
                        purl_matches(sub, subcomponent_purl)):
                    continue
                if statement.get("status") in SUPPRESSING:
                    return statement
                if found is None:
                    found = statement
    return found


# --- Syft SBOM ---

def sbom_product(sbom):
    source = sbom.get("source") or {}
    name, version = source.get("name"), source.get("version")
    if not name:
        return None
    return f"pkg:generic/{name}@{version}" if version else \
        f"pkg:generic/{name}"


def sbom_debs(sbom):
    # yields the installed Debian binary packages, with their source package
    for artifact in sbom.get("artifacts") or []:
        if artifact.get("type") != "deb":
            continue
        metadata = artifact.get("metadata") or {}
        name, version = artifact.get("name"), artifact.get("version")
        source = metadata.get("source") or name
        # Syft only records the source version when it differs
        source_version = metadata.get("sourceVersion") or version
        yield {
            "name": name,
            "version": version,
            "source": source,
            "source_version": source_version,
            "purl": artifact.get("purl") or f"pkg:deb/debian/{name}@{version}",
        }
