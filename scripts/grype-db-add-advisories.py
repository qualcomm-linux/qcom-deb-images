#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# Add our OSV advisories (advisories/*.osv.json on the security-tracker
# branch) to a Grype vulnerability database, as another Debian data source,
# provider "qli"; see docs/workflows.md#qli-advisories.
#
# Grype's database builder only takes OSV records from the providers it knows,
# so this writes them into the SQLite database (schema v6) directly, the way
# the builder writes the Debian provider's records: a vulnerability handle per
# advisory, and an affected package handle per Debian release and source
# package, with dpkg version constraints.
#
# The database is modified in place, in a single transaction; import it with
# "grype db import" afterwards. It fails when the database isn't of the schema
# it knows. The records are pre-validated on the QLI security-tracker already;
# one this can't represent anyway is skipped, rather than failing the scan of
# every image. Warnings about those, and e.g. about vulnerabilities the Debian
# security-tracker covers too by now, are printed to stdout, prefixed with
# "warning:".

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

PROVIDER = "qli"
MODEL_VERSION = 6
# from distro-info-data
DISTRO_INFO = "/usr/share/distro-info/debian.csv"

# the tables and columns written or read here
COLUMNS = {
    "db_metadata": {"model"},
    "providers": {"id", "version", "processor", "date_captured",
                  "input_digest"},
    "blobs": {"id", "value"},
    "vulnerability_handles": {"id", "name", "status", "published_date",
                              "modified_date", "provider_id", "blob_id"},
    "vulnerability_aliases": {"name", "alias"},
    "operating_systems": {"id", "name", "major_version", "minor_version",
                          "label_version", "codename"},
    "operating_system_specifier_overrides": {
        "alias", "version", "codename", "replacement",
        "replacement_major_version", "replacement_label_version"},
    "packages": {"id", "ecosystem", "name"},
    "affected_package_handles": {"id", "vulnerability_id",
                                 "operating_system_id", "package_id",
                                 "blob_id"},
    "unaffected_package_handles": {"vulnerability_id", "operating_system_id",
                                   "package_id"},
}

ECOSYSTEM_RE = re.compile(r"Debian(?::(?P<release>[a-z0-9.-]+))?")
# Debian versions, with colons only after an epoch; Grype silently ignores
# records whose constraints it can't parse
VERSION_RE = re.compile(
    r"[0-9]+:[0-9](?:[A-Za-z0-9.+~:-]*[A-Za-z0-9.+~])?|"
    r"[0-9](?:[A-Za-z0-9.+~-]*[A-Za-z0-9.+~])?")
CVSS_VERSION_RE = re.compile(r"CVSS:(?P<version>[0-9.]+)/")


class AdvisoryError(Exception):
    pass


def db_time(value):
    # RFC 3339 to the format of the database's datetime columns
    if not value:
        return None
    stamp = datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
    return stamp.astimezone(timezone.utc).isoformat(sep=" ")


def check_schema(db):
    for table, columns in COLUMNS.items():
        found = {row[0] for row in db.execute(
            "SELECT name FROM pragma_table_info(?)", (table,))}
        missing = columns - found
        if missing:
            raise AdvisoryError(f"unsupported database: {table} lacks "
                                f"{', '.join(sorted(missing))}")
    models = {row[0] for row in db.execute("SELECT model FROM db_metadata")}
    if models != {MODEL_VERSION}:
        raise AdvisoryError(f"unsupported database schema {models}, "
                            f"expected v{MODEL_VERSION}")
    if db.execute("SELECT 1 FROM providers WHERE id = ?",
                  (PROVIDER,)).fetchone():
        raise AdvisoryError(f"the database has a {PROVIDER} provider "
                            "already")


def load_debian_releases(path):
    # the Debian releases of distro-info-data (debian.csv), as the OSV Debian
    # ecosystem names them: by version, e.g. "13", or by series for those
    # without one, e.g. "sid"; mapped to their series, e.g. "trixie"
    releases = {}
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            releases[row["version"] or row["series"]] = row["series"]
    return releases


def debian_releases(db, ecosystem, releases):
    # the operating system rows of the Debian release(s) of an OSV ecosystem:
    # Debian for all of them, Debian:<release> for one. Grype knows releases
    # by version or codename, and through its overrides, e.g. testing (forky)
    # is matched as unstable, never by its version; so look for the series of
    # the release too
    match = ECOSYSTEM_RE.fullmatch(ecosystem or "")
    if not match:
        raise AdvisoryError(f"unsupported ecosystem {ecosystem!r}")
    rows = db.execute(
        "SELECT id, major_version, minor_version, label_version, codename "
        "FROM operating_systems WHERE name = 'debian'").fetchall()
    release = match["release"]
    if release is None:
        return rows
    if release not in releases:
        raise AdvisoryError(f"unknown Debian release {release!r}")
    series = releases[release]
    wanted = {release, series}
    if series == "sid":
        wanted.add("unstable")
    for major, label in db.execute(
            "SELECT replacement_major_version, replacement_label_version "
            "FROM operating_system_specifier_overrides "
            "WHERE alias = 'debian' AND (version IN (?, ?) OR "
            "codename IN (?, ?)) "
            "AND coalesce(replacement, 'debian') = 'debian'",
            (release, series, release, series)):
        wanted.update(filter(None, (major, label)))
    rows = [row for row in rows if not row[2] and
            wanted & {row[1], row[3], row[4]}]
    if not rows:
        raise AdvisoryError(f"Debian release {release!r} unknown to Grype")
    return rows


def describe_release(row):
    _, major, _, label, codename = row
    return " ".join(filter(None, (major or label, f"({codename})"
                                  if codename else None)))


def range_pairs(osv_range):
    # (introduced, end kind, end version) for each interval of an OSV
    # ECOSYSTEM range, in the order the security-tracker branch checks
    if osv_range.get("type") != "ECOSYSTEM":
        raise AdvisoryError("only ECOSYSTEM ranges are supported")
    pairs = []
    for event in osv_range.get("events") or []:
        (kind, version), = event.items()
        if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
            raise AdvisoryError(f"invalid Debian version {version!r}")
        if kind == "introduced" and (not pairs or pairs[-1][1]):
            pairs.append((version, None, None))
        elif kind in ("fixed", "last_affected") and pairs and \
                not pairs[-1][1]:
            pairs[-1] = (pairs[-1][0], kind, version)
        else:
            raise AdvisoryError(f"unsupported range event {kind!r}")
    if not pairs:
        raise AdvisoryError("range without events")
    return pairs


def grype_range(introduced, kind, version):
    # the database's form of an affected interval; Grype ANDs comma-separated
    # constraints
    constraints = []
    if introduced != "0":
        constraints.append(f">= {introduced}")
    if kind == "fixed":
        constraints.append(f"< {version}")
        fix = {"version": version, "state": "fixed"}
    elif kind == "last_affected":
        constraints.append(f"<= {version}")
        fix = {"state": "unknown"}
    else:
        fix = {"state": "not-fixed"}
    version_spec = {"type": "dpkg"}
    if constraints:
        version_spec["constraint"] = ", ".join(constraints)
    return {"version": version_spec, "fix": fix}


def severities(advisory):
    # Grype reports the first severity, so ours goes first
    severity = (advisory.get("database_specific") or {}).get("severity")
    if not isinstance(severity, str) or not severity:
        raise AdvisoryError("missing database_specific.severity")
    result = [{"scheme": "CHMLN", "value": severity.lower(),
               "source": PROVIDER, "rank": 1}]
    for entry in advisory.get("severity") or []:
        if not entry.get("type", "").startswith("CVSS_V"):
            continue
        vector = entry.get("score", "")
        match = CVSS_VERSION_RE.match(vector)
        version = match["version"] if match else "2.0"
        result.append({"scheme": "CVSS",
                       "value": {"vector": vector, "version": version},
                       "source": PROVIDER, "rank": 1})
    return result


def references(advisory, source_url):
    # Grype reports the first one as the data source
    refs = [{"url": source_url, "tags": ["ADVISORY"]}] if source_url else []
    for ref in advisory.get("references") or []:
        if ref.get("url"):
            refs.append({"url": ref["url"],
                         "tags": [ref["type"]] if ref.get("type") else []})
    return refs


def add_blob(db, value):
    return db.execute("INSERT INTO blobs (value) VALUES (?)",
                      (json.dumps(value, separators=(",", ":")) + "\n",)
                      ).lastrowid


def package_id(db, name):
    db.execute("INSERT OR IGNORE INTO packages (ecosystem, name) "
               "VALUES ('deb', ?)", (name,))
    return db.execute("SELECT id FROM packages WHERE ecosystem = 'deb' "
                      "AND name = ?", (name,)).fetchone()[0]


def tracked_elsewhere(db, names, os_id, pkg_id):
    # the providers that have the vulnerability for the package and release
    # already, as affected or not
    found = set()
    for name in names:
        found.update(row[0] for row in db.execute(
            "SELECT v.provider_id FROM affected_package_handles h "
            "JOIN vulnerability_handles v ON v.id = h.vulnerability_id "
            "WHERE v.name = ? AND v.provider_id != ? "
            "AND h.operating_system_id = ? AND h.package_id = ? "
            "UNION "
            "SELECT v.provider_id FROM unaffected_package_handles h "
            "JOIN vulnerability_handles v ON v.id = h.vulnerability_id "
            "WHERE v.name = ? AND v.provider_id != ? "
            "AND h.operating_system_id = ? AND h.package_id = ?",
            (name, PROVIDER, os_id, pkg_id) * 2))
    return sorted(found)


def add_advisory(db, advisory, releases, source_url, warn):
    advisory_id = advisory["id"]
    aliases = sorted(set(advisory.get("aliases") or []))
    summary = advisory.get("summary", "").strip()
    details = advisory.get("details", "").strip()
    blob_id = add_blob(db, {
        "id": advisory_id,
        "description": "\n\n".join(filter(None, (summary, details))),
        "refs": references(advisory, source_url),
        "aliases": aliases,
        "severities": severities(advisory),
    })
    vuln_id = db.execute(
        "INSERT INTO vulnerability_handles (name, status, published_date, "
        "modified_date, provider_id, blob_id) "
        "VALUES (?, 'active', ?, ?, ?, ?)",
        (advisory_id, db_time(advisory.get("published")),
         db_time(advisory.get("modified")), PROVIDER, blob_id)).lastrowid
    for alias in aliases:
        db.execute("INSERT OR IGNORE INTO vulnerability_aliases (name, alias) "
                   "VALUES (?, ?)", (advisory_id, alias))

    # one handle per release and source package, with the ranges of every
    # entry
    ranges = {}
    for entry in advisory.get("affected") or []:
        package = entry.get("package") or {}
        source = package.get("name")
        if not source:
            raise AdvisoryError("affected entry without a package name")
        if "ecosystem_specific" in entry:
            # advisories cover every binary of the source package, like the
            # Debian security tracker's data
            raise AdvisoryError(f"{source}: ecosystem_specific is not "
                                "supported")
        entry_ranges = [grype_range(*pair)
                        for osv_range in entry.get("ranges") or []
                        for pair in range_pairs(osv_range)]
        if not entry_ranges:
            raise AdvisoryError(f"{source}: no ranges")
        for release in debian_releases(db, package.get("ecosystem"),
                                       releases):
            ranges.setdefault((release, source), []).extend(entry_ranges)
    if not ranges:
        raise AdvisoryError("no affected packages")

    cves = [name for name in (advisory_id, *aliases)
            if name.startswith("CVE-")]
    added = []
    for (release, source), handle_ranges in sorted(ranges.items()):
        pkg_id = package_id(db, source)
        db.execute(
            "INSERT INTO affected_package_handles (vulnerability_id, "
            "operating_system_id, package_id, blob_id) VALUES (?, ?, ?, ?)",
            (vuln_id, release[0], pkg_id,
             add_blob(db, {"ranges": handle_ranges})))
        added.append(f"{source} on debian {describe_release(release)}")
        providers = tracked_elsewhere(db, cves, release[0], pkg_id) \
            if cves else []
        if providers:
            warn(f"{advisory_id}: {'/'.join(cves)} for {source} on debian "
                 f"{describe_release(release)} is tracked by "
                 f"{', '.join(providers)} now; remove the advisory")
    return added


def input_digest(paths, root):
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda p: os.path.relpath(p, root)):
        digest.update(os.path.relpath(path, root).encode() + b"\0")
        with open(path, "rb") as f:
            digest.update(f.read() + b"\0")
    return f"sha256:{digest.hexdigest()}"


def run(args):
    def warn(message):
        print(f"warning: {message}")

    if not os.path.isfile(args.db):
        raise AdvisoryError(f"no database at {args.db}")
    releases = {}
    if args.advisories:
        try:
            releases = load_debian_releases(args.distro_info)
        except (OSError, KeyError, csv.Error) as error:
            raise AdvisoryError(f"cannot read the Debian releases of "
                                f"distro-info-data: {error}") from error
    db = sqlite3.connect(args.db)
    try:
        check_schema(db)
        with db:
            db.execute(
                "INSERT INTO providers (id, version, processor, "
                "date_captured, input_digest) VALUES (?, '1', ?, ?, ?)",
                (PROVIDER, os.path.basename(__file__),
                 datetime.now(timezone.utc).isoformat(sep=" "),
                 input_digest(args.advisories, args.source_root)))
            for path in args.advisories:
                source_url = None
                if args.source_url:
                    source_url = args.source_url + \
                        os.path.relpath(path, args.source_root)
                # each advisory is added entirely, or not at all
                db.execute("SAVEPOINT advisory")
                try:
                    with open(path, encoding="utf-8") as f:
                        advisory = json.load(f)
                    if advisory.get("withdrawn"):
                        print(f"{advisory.get('id')}: withdrawn, skipped")
                        added = None
                    else:
                        added = add_advisory(db, advisory, releases,
                                             source_url, warn)
                except (AdvisoryError, AttributeError, KeyError,
                        TypeError, ValueError) as error:
                    db.execute("ROLLBACK TO advisory")
                    warn(f"{path}: skipped: {error}")
                    added = None
                db.execute("RELEASE advisory")
                if added:
                    print(f"{advisory['id']}: {', '.join(added)}")
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(
        description="Add QLI OSV advisories to a Grype vulnerability "
                    "database, in place.")
    parser.add_argument("db", help="Grype vulnerability.db (schema v6)")
    parser.add_argument("advisories", nargs="*", help="*.osv.json files")
    parser.add_argument("--source-url",
                        help="URL prefix of the advisories' published "
                             "location, followed by their path relative to "
                             "--source-root; reported by Grype as the data "
                             "source of their findings")
    parser.add_argument("--distro-info", default=DISTRO_INFO,
                        help="Debian releases of distro-info-data "
                             "(default: %(default)s)")
    parser.add_argument("--source-root", default=".",
                        help="the directory --source-url points at "
                             "(default: the current one)")
    args = parser.parse_args()
    try:
        run(args)
    except (AdvisoryError, OSError, ValueError, sqlite3.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
