#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# lint our OpenVEX documents (vex/*.openvex.json), see vex/README.md: they are
# validated against the OpenVEX JSON schema, then checked against our rules
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

CVE_RE = re.compile(r"^CVE-[0-9]{4}-[0-9]{4,}$")


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
    if not CVE_RE.fullmatch(name):
        report.error(where, f"vulnerability name {name!r} must be a CVE "
                            "identifier")
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
        description="Check OpenVEX documents.")
    parser.add_argument("files", nargs="*",
                        help="*.openvex.json files")
    parser.add_argument("--schema", default=SCHEMA,
                        help="OpenVEX JSON schema (default: the file "
                             "downloaded by make vex-schema)")
    parser.add_argument("--sbom", help="Syft JSON SBOM to check the "
                        "statements against (warnings only)")
    parser.add_argument("--coverage-only", action="store_true",
                        help="only check SBOM coverage of pre-validated "
                             "documents; requires --sbom, no schema needed")
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
    documents = []
    for path in args.files:
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
