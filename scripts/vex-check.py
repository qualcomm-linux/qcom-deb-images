#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# lint our OpenVEX documents (vex/*.openvex.json) and OSV advisories
# (advisories/*.osv.json)
#
# rule violations are errors and make the script exit non-zero. with --sbom,
# the documents are also checked against the packages installed in a build;
# that only produces warnings

import argparse
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import vexlib

CVE_RE = re.compile(r"^CVE-[0-9]{4}-[0-9]{4,}$")
ADVISORY_ID_RE = re.compile(r"^QLI-(CVE-[0-9]{4}-[0-9]{4,}|[0-9]{4}-[0-9]+)$")
ECOSYSTEM_RE = re.compile(r"^Debian(:[A-Za-z0-9.]+)?$")
SEVERITIES = ("Critical", "High", "Medium", "Low")


class Report:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, where, message):
        self.errors.append(f"{where}: {message}")

    def warning(self, where, message):
        self.warnings.append(f"{where}: {message}")


def parse_time(value):
    if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?"
            r"(?:[Zz]|[+-]\d{2}:\d{2})", value):
        return None
    try:
        return datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
    except ValueError:
        return None


def object_list(value, where, report):
    if not isinstance(value, list) or not all(
            isinstance(item, dict) for item in value):
        report.error(where, "must be an array of objects")
        return []
    return value


# --- OpenVEX ---

def check_openvex(path, doc, report, versioned_product_ok, max_age, now):
    if doc.get("@context") != vexlib.OPENVEX_CONTEXT:
        report.error(path, f"@context must be {vexlib.OPENVEX_CONTEXT}")
    for key in ("@id", "author"):
        if not isinstance(doc.get(key), str) or not doc[key].strip():
            report.error(path, f"{key} must be a non-empty string")
    if type(doc.get("version")) is not int or doc["version"] < 1:
        report.error(path, "version must be a positive integer")
    doc_time = parse_time(doc.get("timestamp"))
    if doc_time is None:
        report.error(path, "timestamp is not an RFC 3339 date")

    statements = object_list(doc.get("statements"),
                             f"{path}: statements", report)
    for index, statement in enumerate(statements):
        where = f"{path}: statement {index}"
        check_statement(where, statement, doc_time, report,
                        versioned_product_ok, max_age, now)


def check_statement(where, statement, doc_time, report,
                    versioned_product_ok, max_age, now):
    vuln = statement.get("vulnerability")
    if not isinstance(vuln, dict):
        report.error(where, "vulnerability must be an object")
        vuln = {}
    name = vuln.get("name")
    if not isinstance(name, str) or not (
            CVE_RE.fullmatch(name) or ADVISORY_ID_RE.fullmatch(name)):
        report.error(where, f"vulnerability name {name!r} must be a CVE "
                            "identifier (or a QLI advisory ID)")
    where = f"{where} ({name})"
    aliases = vuln.get("aliases", [])
    if not isinstance(aliases, list) or not all(
            isinstance(alias, str) and alias for alias in aliases):
        report.error(where, "vulnerability aliases must be strings")

    status = statement.get("status")
    if status not in vexlib.VEX_STATUSES:
        report.error(where, f"invalid status {status!r}")

    products = object_list(statement.get("products"),
                           f"{where}: products", report)
    if not products:
        report.error(where, "no products")
    for product in products:
        product_id = product.get("@id") or ""
        allowed = product_id == vexlib.PRODUCT or (
            versioned_product_ok and isinstance(product_id, str) and
            product_id.startswith(vexlib.PRODUCT + "@") and
            len(product_id) > len(vexlib.PRODUCT) + 1)
        if not allowed:
            report.error(where, f"product must be {vexlib.PRODUCT} without a "
                                f"version, not {product_id!r}")
        subs = object_list(product.get("subcomponents"),
                           f"{where}: subcomponents", report)
        if not subs:
            report.error(where, "product has no subcomponents: list the "
                                "binary packages")
        for sub in subs:
            purl = vexlib.parse_purl(sub.get("@id") or "")
            if not purl or purl.type != "deb" or not purl.namespace or \
                    not purl.name:
                report.error(where, f"subcomponent {sub.get('@id')!r} must "
                                    "be a pkg:deb/<namespace>/<binary> purl")
                continue
            if status == "fixed" and not purl.version:
                report.error(where, "fixed needs every subcomponent pinned "
                                    f"to its fixed version: {sub['@id']}")

    stamp = doc_time
    if "timestamp" in statement:
        stamp = parse_time(statement["timestamp"])
        if stamp is None:
            report.error(where, "timestamp is not an RFC 3339 date")
    if status == "not_affected":
        justification = statement.get("justification")
        if justification not in vexlib.JUSTIFICATIONS:
            report.error(where, "not_affected needs a valid justification, "
                                f"not {justification!r}")
        impact = statement.get("impact_statement")
        if not isinstance(impact, str) or not impact.strip():
            report.error(where, "not_affected needs an impact_statement")
    elif status == "affected":
        action = statement.get("action_statement")
        if not isinstance(action, str) or not action.strip():
            report.error(where, "affected needs an action_statement")
    elif status == "under_investigation" and max_age:
        if stamp and now - stamp > max_age:
            report.warning(where, "under_investigation for more than "
                                  f"{max_age.days} days")


def check_uniqueness(documents, report):
    seen = defaultdict(list)
    for path, doc in documents:
        for statement in doc.get("statements") or []:
            for vuln_id in vexlib.statement_vulnerability_ids(statement):
                for product, sub in vexlib.statement_purls(statement):
                    for old_product, old_sub, old_path in seen[vuln_id]:
                        if (vexlib.purls_overlap(product, old_product) and
                                vexlib.purls_overlap(sub, old_sub)):
                            report.error(path, f"{vuln_id} for {sub} is "
                                               f"already covered in "
                                               f"{old_path} ({old_sub}); "
                                               "edit the existing statement")
                            break
                    seen[vuln_id].append((product, sub, path))


# --- OSV advisories ---

def object_value(value, where, report):
    if not isinstance(value, dict):
        report.error(where, "must be an object")
        return {}
    return value


def check_advisory(path, advisory, report):
    vuln_id = advisory.get("id")
    if not isinstance(vuln_id, str) or not ADVISORY_ID_RE.fullmatch(vuln_id):
        report.error(path, f"id {vuln_id!r} must be QLI-<CVE> or "
                           "QLI-<year>-<n>")
    elif Path(path).name != f"{vuln_id}.osv.json":
        report.error(path, f"file name must be {vuln_id}.osv.json")
    if "modified" not in advisory:
        report.error(path, "missing modified")
    for key in ("published", "modified", "withdrawn"):
        if key in advisory and parse_time(advisory[key]) is None:
            report.error(path, f"{key} is not an RFC 3339 date")
    if "withdrawn" in advisory:
        return
    summary = advisory.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        report.error(path, "summary must be a non-empty string")
    if "details" in advisory and not isinstance(advisory["details"], str):
        report.error(path, "details must be a string")
    aliases = advisory.get("aliases", [])
    if not isinstance(aliases, list) or not all(
            isinstance(alias, str) and alias.strip() for alias in aliases):
        report.error(path, "aliases must be an array of non-empty strings")
        aliases = []
    for alias in aliases:
        if alias.startswith("CVE-") and not CVE_RE.fullmatch(alias):
            report.error(path, f"invalid CVE alias {alias!r}")
    if isinstance(vuln_id, str) and vuln_id.startswith("QLI-CVE-") and \
            vuln_id[4:] not in aliases:
        report.error(path, f"aliases must include {vuln_id[4:]}")

    metadata = object_value(advisory.get("database_specific", {}),
                            f"{path}: database_specific", report)
    severity = metadata.get("severity")
    if severity not in SEVERITIES:
        report.error(path, "database_specific.severity must be one of "
                           f"{', '.join(SEVERITIES)}")

    for field, keys in (("references", ("type", "url")),
                        ("severity", ("type", "score"))):
        for item in object_list(advisory.get(field, []),
                                f"{path}: {field}", report):
            for key in keys:
                if not isinstance(item.get(key), str) or not item[key]:
                    report.error(path, f"{field}.{key} must be a "
                                       "non-empty string")
    affected = object_list(advisory.get("affected"),
                           f"{path}: affected", report)
    if not affected:
        report.error(path, "no affected packages")
    for entry in affected:
        check_affected(path, entry, report)


def check_affected(path, entry, report):
    package = object_value(entry.get("package"), f"{path}: package", report)
    ecosystem = package.get("ecosystem")
    if not isinstance(ecosystem, str) or not ECOSYSTEM_RE.fullmatch(ecosystem):
        report.error(path, f"ecosystem {ecosystem!r} must be "
                           "Debian or Debian:<release>")
    name = package.get("name")
    if not isinstance(name, str) or not name.strip():
        report.error(path, "package without a name (the source package)")
    metadata = object_value(entry.get("ecosystem_specific", {}),
                            f"{path}: ecosystem_specific", report)
    binaries = metadata.get("binaries")
    if binaries is not None and not (
            isinstance(binaries, list) and binaries and
            all(isinstance(b, str) and b.strip() for b in binaries)):
        report.error(path, "ecosystem_specific.binaries must be a list of "
                           "binary package names")
    if "binaries" in metadata and binaries is None:
        report.error(path, "ecosystem_specific.binaries cannot be null")
    if "versions" in entry:
        report.error(path, "use ranges, not a versions list")
    ranges = object_list(entry.get("ranges"), f"{path}: ranges", report)
    if not ranges:
        report.error(path, f"{package.get('name')}: no ranges")
    for rng in ranges:
        try:
            vexlib.osv_range_events(rng)
        except ValueError as error:
            report.error(path, str(error))


# --- SBOM coverage ---

def check_sbom(documents, sbom, report):
    debs = list(vexlib.sbom_debs(sbom))
    by_source = defaultdict(list)
    for deb in debs:
        by_source[deb["source"]].append(deb)

    for path, doc in documents:
        for index, statement in enumerate(doc.get("statements") or []):
            name = (statement.get("vulnerability") or {}).get("name")
            where = f"{path}: statement {index} ({name})"
            matched = []
            for _, sub in vexlib.statement_purls(statement):
                hits = [d for d in debs
                        if vexlib.purl_matches(sub, d["purl"])]
                if not hits:
                    report.warning(where, f"{sub} matches nothing in this "
                                          "SBOM (package gone, or pinned "
                                          "version outdated)")
                matched.extend(hits)
            covered = {d["name"] for d in matched}
            for source in {d["source"] for d in matched}:
                missing = sorted(d["name"] for d in by_source[source]
                                 if d["name"] not in covered)
                if missing:
                    report.warning(where, f"binaries of src:{source} not "
                                          f"covered: {', '.join(missing)}")


def main():
    parser = argparse.ArgumentParser(
        description="Check OpenVEX documents and OSV advisories.")
    parser.add_argument("files", nargs="*",
                        help="*.openvex.json and *.osv.json files")
    parser.add_argument("--sbom", help="Syft JSON SBOM to check the "
                        "statements against (warnings only). The product may "
                        "then carry a version, as in the merged document.")
    parser.add_argument("--max-age-days", type=int, default=90,
                        help="warn about under_investigation statements "
                             "older than this (default: %(default)s)")
    args = parser.parse_args()

    report = Report()
    now = datetime.now(timezone.utc)
    max_age = timedelta(days=args.max_age_days)
    documents = []
    advisory_ids = {}
    for path in args.files:
        try:
            doc = vexlib.load_json(path)
        except (OSError, ValueError) as e:
            report.error(path, f"cannot read: {e}")
            continue
        if isinstance(doc, dict) and "@context" in doc:
            errors_before = len(report.errors)
            check_openvex(path, doc, report, bool(args.sbom), max_age, now)
            if len(report.errors) == errors_before:
                documents.append((path, doc))
        elif isinstance(doc, dict) and "id" in doc:
            errors_before = len(report.errors)
            check_advisory(path, doc, report)
            if (len(report.errors) == errors_before and
                    not doc.get("withdrawn")):
                for vuln_id in {doc["id"], *doc.get("aliases", [])}:
                    if vuln_id in advisory_ids:
                        report.error(path, f"{vuln_id} is already recorded "
                                           f"in {advisory_ids[vuln_id]}")
                    advisory_ids[vuln_id] = path
        else:
            report.error(path, "neither an OpenVEX document nor an OSV "
                               "advisory")
    check_uniqueness(documents, report)

    if args.sbom:
        try:
            check_sbom(documents, vexlib.load_json(args.sbom), report)
        except (OSError, ValueError) as e:
            report.warning(args.sbom, f"cannot read SBOM: {e}")

    for line in report.errors:
        print(f"error: {line}")
    for line in report.warnings:
        print(f"warning: {line}")
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
