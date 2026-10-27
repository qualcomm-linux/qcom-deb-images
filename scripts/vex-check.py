#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# lint our OpenVEX documents (vex/*.openvex.json), see vex/README.md, and our
# OSV advisories (advisories/*.osv.json), see advisories/README.md: they are
# validated against the OpenVEX or OSV JSON schema, then checked against our
# rules. files named *.osv.json are advisories.
#
# rule violations are errors and make the script exit non-zero. with --sbom,
# the documents are also checked against the packages installed in a build;
# that only produces warnings

import argparse
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import vexlib

# Downloaded by `make vex-schema`; resolve relative to this script so the
# checker also works outside the repository's working directory.
SCHEMA = os.path.join(os.path.dirname(__file__), os.pardir, ".cache",
                      "openvex_json_schema.json")
# downloaded by `make osv-schema`
OSV_SCHEMA = os.path.join(os.path.dirname(__file__), os.pardir, ".cache",
                          "osv_schema.json")
# from distro-info-data
DISTRO_INFO = "/usr/share/distro-info/debian.csv"

CVE_RE = re.compile(r"^CVE-[0-9]{4}-[0-9]{4,}$")

ADVISORY_SUFFIX = ".osv.json"
SEVERITIES = ("Critical", "High", "Medium", "Low")
CVSS_TYPES = ("CVSS_V2", "CVSS_V3", "CVSS_V4")
# Debian for every release, or Debian:<release>, with the release as the OSV
# schema wants it: its version in distro-info-data, or its series for the
# releases without one, e.g. sid
ECOSYSTEM_RE = re.compile(r"Debian(?::(?P<release>[a-z0-9.-]+))?")


class Report:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, where, message):
        self.errors.append(f"{where}: {message}")

    def warning(self, where, message):
        self.warnings.append(f"{where}: {message}")


# --- OpenVEX ---

def check_schema(path, doc, validator, report):
    # the structure, statuses, justifications and required fields; the rest
    # of the checks rely on it
    errors = sorted(validator.iter_errors(doc),
                    key=lambda e: list(map(str, e.path)))
    for error in errors:
        where = "/".join(str(p) for p in error.absolute_path)
        report.error(f"{path}: {where}" if where else path, error.message)
    return not errors


def check_openvex(path, doc, report, max_age, now):
    if doc["@context"] != vexlib.OPENVEX_CONTEXT:
        report.error(path, f"@context must be {vexlib.OPENVEX_CONTEXT}")
    # the schema has the timestamps as date-time, but jsonschema only checks
    # formats with extra modules
    doc_time = vexlib.parse_time(doc["timestamp"])
    if doc_time is None:
        report.error(path, "timestamp is not an RFC 3339 date")

    for index, statement in enumerate(doc["statements"]):
        where = f"{path}: statement {index}"
        check_statement(where, statement, doc_time, report, max_age, now)


def check_statement(where, statement, doc_time, report, max_age, now):
    name = statement["vulnerability"]["name"]
    advisory = vexlib.ADVISORY_ID_RE.fullmatch(name)
    if advisory and advisory["cve"]:
        # Grype reports these as the CVE, as the scan uses --by-cve
        report.error(where, f"vulnerability name {name!r} must be "
                            f"{advisory['cve']}, as Grype reports it")
    elif not CVE_RE.fullmatch(name) and not advisory:
        report.error(where, f"vulnerability name {name!r} must be a CVE "
                            "identifier, or the ID of a QLI advisory "
                            "without a CVE")
    where = f"{where} ({name})"

    status = statement["status"]
    products = statement.get("products", [])
    if not products:
        report.error(where, "no products")
    for product in products:
        product_id = product.get("@id")
        if product_id != vexlib.PRODUCT:
            report.error(where, f"product must be {vexlib.PRODUCT} without a "
                                f"version, not {product_id!r}")
        subs = product.get("subcomponents", [])
        if not subs:
            report.error(where, "product has no subcomponents: list the "
                                "binary packages")
        for sub in subs:
            parsed = vexlib.parse_subcomponent(sub.get("@id"))
            if not parsed:
                report.error(where, f"subcomponent {sub.get('@id')!r} must "
                                    "be pkg:deb/debian/<binary>[@<version>], "
                                    "without qualifiers or percent-encoding")
                continue
            if status == "fixed" and not parsed[1]:
                report.error(where, "fixed needs every subcomponent pinned "
                                    f"to its fixed version: {sub['@id']}")

    stamp = doc_time
    if "timestamp" in statement:
        stamp = vexlib.parse_time(statement["timestamp"])
        if stamp is None:
            report.error(where, "timestamp is not an RFC 3339 date")
    # the schema only wants one of justification and impact_statement
    if status == "not_affected":
        for key in ("justification", "impact_statement"):
            if not statement.get(key, "").strip():
                report.error(where, f"not_affected needs a {key}")
    elif status == "affected":
        if not statement["action_statement"].strip():
            report.error(where, "affected needs an action_statement")
    elif status == "under_investigation" and max_age:
        if stamp and now - stamp > max_age:
            report.warning(where, "under_investigation for more than "
                                  f"{max_age.days} days")


def check_uniqueness(documents, report):
    # a versionless subcomponent overlaps every version of the binary
    seen = defaultdict(list)
    for path, doc in documents:
        for statement in doc.get("statements") or []:
            for vuln_id in vexlib.statement_vulnerability_ids(statement):
                for sub in vexlib.statement_subcomponents(statement):
                    name, version = vexlib.parse_subcomponent(sub)
                    for old_name, old_version, old_sub, old_path in \
                            seen[vuln_id]:
                        if name == old_name and (
                                not version or not old_version or
                                version == old_version):
                            report.error(path, f"{vuln_id} for {sub} is "
                                               f"already covered in "
                                               f"{old_path} ({old_sub}); "
                                               "edit the existing statement")
                            break
                    seen[vuln_id].append((name, version, sub, path))


def check_advisory_references(documents, advisory_ids, report):
    # statements about QLI advisories have to name one that exists
    for path, doc in documents:
        for index, statement in enumerate(doc.get("statements") or []):
            name = statement["vulnerability"]["name"]
            if vexlib.ADVISORY_ID_RE.fullmatch(name) and \
                    name not in advisory_ids:
                report.error(f"{path}: statement {index} ({name})",
                             "no such advisory; pass the advisories "
                             f"(advisories/*{ADVISORY_SUFFIX}) too")


# --- OSV advisories ---

def load_osv_validator(path):
    # QLI isn't a registered OSV database prefix, which the schema checks
    # identifiers against; ADVISORY_ID_RE is checked instead, see
    # advisories/README.md
    import jsonschema  # pylint: disable=import-outside-toplevel

    schema = vexlib.load_json(path)
    schema["properties"]["id"] = {"type": "string"}
    return jsonschema.Draft202012Validator(schema)


def check_advisory(path, doc, validator, releases, report):
    # returns the IDs the advisory claims, i.e. its own and its aliases, or
    # None when it's invalid
    errors_before = len(report.errors)
    if not check_schema(path, doc, validator, report):
        return None
    advisory_id = doc["id"]
    match = vexlib.ADVISORY_ID_RE.fullmatch(advisory_id)
    if not match:
        report.error(path, f"id {advisory_id!r} must be QLI-<CVE> or "
                           "QLI-<year>-<n>")
        return None
    if os.path.basename(path) != advisory_id + ADVISORY_SUFFIX:
        report.error(path, f"file name must be {advisory_id}"
                           f"{ADVISORY_SUFFIX}")
    for key in ("published", "modified", "withdrawn"):
        if key in doc and vexlib.parse_time(doc[key]) is None:
            report.error(path, f"{key} is not an RFC 3339 date")
    if "withdrawn" in doc:
        return {advisory_id} if len(report.errors) == errors_before \
            else None

    if not (doc.get("summary") or "").strip():
        report.error(path, "summary must be a non-empty string")
    aliases = doc.get("aliases") or []
    cves = {alias for alias in aliases if alias.startswith("CVE-")}
    for alias in aliases:
        if alias.startswith("CVE-") and not CVE_RE.fullmatch(alias):
            report.error(path, f"invalid CVE alias {alias!r}")
        if alias.startswith("QLI-"):
            report.error(path, f"alias {alias!r} is a QLI advisory")
    if match["cve"] and cves != {match["cve"]}:
        report.error(path, f"aliases must include {match['cve']} and no "
                           "other CVE")
    elif not match["cve"] and cves:
        report.error(path, f"rename the advisory to QLI-{min(cves)}, as it "
                           "has a CVE now")
    severity = (doc.get("database_specific") or {}).get("severity")
    if severity not in SEVERITIES:
        report.error(path, "database_specific.severity must be one of "
                           f"{', '.join(SEVERITIES)}")
    for entry in doc.get("severity") or []:
        if entry["type"] not in CVSS_TYPES:
            report.error(path, f"severity type must be one of "
                               f"{', '.join(CVSS_TYPES)}")

    affected = doc.get("affected") or []
    if not affected:
        report.error(path, "no affected packages")
    for entry in affected:
        check_affected(path, entry, releases, report)
    if len(report.errors) != errors_before:
        return None
    return {advisory_id, *aliases}


def check_affected(path, entry, releases, report):
    package = entry.get("package")
    if not package:
        report.error(path, "affected entry without a package")
        return
    ecosystem, source = package["ecosystem"], package["name"]
    where = f"{path}: {ecosystem}/{source}"
    match = ECOSYSTEM_RE.fullmatch(ecosystem)
    numbered, unnumbered = releases
    release = match["release"] if match else None
    if not match:
        report.error(where, "ecosystem must be Debian or Debian:<release>")
    elif release and release not in numbered and release not in unnumbered:
        versions = [v for v, series in numbered.items() if series == release]
        if versions:
            report.error(where, f"name the release by its version: "
                                f"Debian:{versions[0]}")
        else:
            report.error(where, f"unknown Debian release {release!r}")
    if not vexlib.PACKAGE_RE.fullmatch(source):
        report.error(where, "package name must be a Debian source package")
    for key in ("versions", "severity", "database_specific"):
        if key in entry:
            report.error(where, f"{key} is not supported here")
    if "ecosystem_specific" in entry:
        # like the Debian security tracker's data
        report.error(where, "ecosystem_specific is not supported: an "
                            "advisory covers every binary of the source "
                            "package; record not_affected statements for "
                            "the binaries that aren't affected")
    ranges = entry.get("ranges") or []
    if not ranges:
        report.error(where, "no ranges")
    for osv_range in ranges:
        try:
            vexlib.range_pairs(osv_range)
        except ValueError as e:
            report.error(where, str(e))


def check_advisory_uniqueness(advisories, report):
    seen = {}
    for path, ids in advisories:
        for vuln_id in sorted(ids):
            if vuln_id in seen:
                report.error(path, f"{vuln_id} is already claimed by "
                                   f"{seen[vuln_id]}")
            seen[vuln_id] = path


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
            for sub in vexlib.statement_subcomponents(statement):
                name, version = vexlib.parse_subcomponent(sub)
                hits = [d for d in debs if d["name"] == name and
                        version in (None, d["version"])]
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
    parser.add_argument("--schema", default=SCHEMA,
                        help="OpenVEX JSON schema (default: the file "
                             "downloaded by make vex-schema)")
    parser.add_argument("--distro-info", default=DISTRO_INFO,
                        help="Debian releases of distro-info-data, needed for "
                             "*.osv.json files (default: %(default)s)")
    parser.add_argument("--osv-schema", default=OSV_SCHEMA,
                        help="OSV JSON schema, needed for *.osv.json files "
                             "(default: the file downloaded by make "
                             "osv-schema)")
    parser.add_argument("--sbom", help="Syft JSON SBOM to check the "
                        "statements against (warnings only)")
    parser.add_argument("--coverage-only", action="store_true",
                        help="only check SBOM coverage of pre-validated "
                             "documents; requires --sbom, no schema needed, "
                             "OSV advisories are ignored")
    parser.add_argument("--max-age-days", type=int, default=90,
                        help="warn about under_investigation statements "
                             "older than this (default: %(default)s)")
    args = parser.parse_args()
    if args.coverage_only and not args.sbom:
        parser.error("--coverage-only requires --sbom")

    report = Report()
    now = datetime.now(timezone.utc)
    max_age = timedelta(days=args.max_age_days)
    if not args.coverage_only:
        # Image builds only check coverage and do not need jsonschema.
        import jsonschema  # pylint: disable=import-outside-toplevel

        try:
            schema = vexlib.load_json(args.schema)
        except (OSError, ValueError) as e:
            parser.error(f"cannot read OpenVEX schema: {e}; "
                         "run 'make vex-schema' or pass --schema PATH")
        validator = jsonschema.Draft202012Validator(schema)
    osv_validator, releases = None, ({}, set())
    advisory_paths = [path for path in args.files
                      if path.endswith(ADVISORY_SUFFIX)]
    if args.coverage_only:
        advisory_paths = []
    elif advisory_paths:
        try:
            osv_validator = load_osv_validator(args.osv_schema)
        except (OSError, ValueError, KeyError) as e:
            parser.error(f"cannot read OSV schema: {e}; "
                         "run 'make osv-schema' or pass --osv-schema PATH")
        try:
            releases = vexlib.debian_releases(args.distro_info)
        except (OSError, ValueError, KeyError) as e:
            parser.error(f"cannot read the Debian releases: {e}; install "
                         "distro-info-data or pass --distro-info PATH")
    documents, advisories = [], []
    for path in args.files:
        if path.endswith(ADVISORY_SUFFIX):
            if not args.coverage_only:
                try:
                    doc = vexlib.load_json(path)
                except (OSError, ValueError) as e:
                    report.error(path, f"cannot read: {e}")
                    continue
                ids = check_advisory(path, doc, osv_validator, releases,
                                     report)
                if ids is not None:
                    advisories.append((path, ids))
            continue
        try:
            doc = vexlib.load_json(path)
        except (OSError, ValueError) as e:
            report.error(path, f"cannot read: {e}")
            continue
        if not args.coverage_only:
            if not check_schema(path, doc, validator, report):
                continue
            errors_before = len(report.errors)
            check_openvex(path, doc, report, max_age, now)
            if len(report.errors) != errors_before:
                continue
        documents.append((path, doc))
    if not args.coverage_only:
        check_uniqueness(documents, report)
        check_advisory_uniqueness(advisories, report)
        check_advisory_references(
            documents, set().union(*(ids for _, ids in advisories)), report)

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
