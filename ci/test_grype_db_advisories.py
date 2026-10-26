"""Tests for adding QLI advisories to the Grype vulnerability database"""

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

import json
import os
import sqlite3
import subprocess
import sys

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), os.pardir, "scripts",
                      "grype-db-add-advisories.py")

# the tables of a Grype v6 database the script uses, as Grype creates them
SCHEMA = """
CREATE TABLE `db_metadata` (`build_timestamp` datetime NOT NULL,`model` integer NOT NULL,`revision` integer NOT NULL,`addition` integer NOT NULL);
CREATE TABLE `providers` (`id` text,`version` text,`processor` text,`date_captured` datetime,`input_digest` text,PRIMARY KEY (`id`));
CREATE TABLE `blobs` (`id` integer PRIMARY KEY AUTOINCREMENT,`value` text NOT NULL);
CREATE TABLE `vulnerability_handles` (`id` integer PRIMARY KEY AUTOINCREMENT,`name` text NOT NULL,`status` text NOT NULL,`published_date` datetime,`modified_date` datetime,`withdrawn_date` datetime,`provider_id` text NOT NULL,`blob_id` integer);
CREATE TABLE `vulnerability_aliases` (`name` text,`alias` text NOT NULL,PRIMARY KEY (`name`,`alias`));
CREATE TABLE `operating_systems` (`id` integer PRIMARY KEY AUTOINCREMENT,`name` text,`release_id` text,`major_version` text,`minor_version` text,`label_version` text,`codename` text,`channel` text,`eol_date` datetime,`eoas_date` datetime);
CREATE TABLE `operating_system_specifier_overrides` (`alias` text,`version` text,`version_pattern` text,`codename` text,`channel` text,`replacement` text,`replacement_major_version` text,`replacement_minor_version` text,`replacement_label_version` text,`replacement_channel` text,`rolling` numeric,`applicable_client_db_schemas` text);
CREATE TABLE `packages` (`id` integer PRIMARY KEY AUTOINCREMENT,`ecosystem` text,`name` text);
CREATE UNIQUE INDEX `idx_package` ON `packages`(`ecosystem` COLLATE NOCASE,`name` COLLATE NOCASE);
CREATE TABLE `affected_package_handles` (`id` integer PRIMARY KEY AUTOINCREMENT,`vulnerability_id` integer NOT NULL,`operating_system_id` integer,`package_id` integer,`blob_id` integer);
CREATE TABLE `unaffected_package_handles` (`id` integer PRIMARY KEY AUTOINCREMENT,`vulnerability_id` integer NOT NULL,`operating_system_id` integer,`package_id` integer,`blob_id` integer);
"""  # noqa: E501  # pylint: disable=line-too-long

# operating system IDs
BOOKWORM, TRIXIE, UNSTABLE, NOBLE, FORKY = 1, 2, 3, 4, 5

# an excerpt of distro-info-data's debian.csv
DISTRO_INFO = """\
version,codename,series,created,release,eol,eol-lts,eol-elts
2.0,Hamm,hamm,1997-06-05,1998-07-24,2000-03-09
12,Bookworm,bookworm,2021-08-14,2023-06-10,2026-07-11,2028-06-30,2033-06-30
13,Trixie,trixie,2023-06-10,2025-08-09,2028-08-09,2030-06-30,2035-06-30
14,Forky,forky,2025-08-09
,Sid,sid,1993-08-16
"""


def make_db(tmp_path, model=6):
    (tmp_path / "debian.csv").write_text(DISTRO_INFO)
    path = str(tmp_path / "vulnerability.db")
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    db.execute("INSERT INTO db_metadata VALUES "
               "('2026-10-26 06:38:44+00:00', ?, 1, 10)", (model,))
    db.execute("INSERT INTO providers VALUES ('debian', '1', 'vunnel', "
               "'2026-10-26 00:32:23+00:00', 'xxh64:0')")
    db.executemany(
        "INSERT INTO operating_systems (id, name, release_id, "
        "major_version, minor_version, label_version, codename) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)", [
            (BOOKWORM, "debian", "debian", "12", "", "", "bookworm"),
            (TRIXIE, "debian", "debian", "13", "", "", "trixie"),
            (UNSTABLE, "debian", "debian", "", "", "unstable", ""),
            (NOBLE, "ubuntu", "ubuntu", "24", "04", "", "noble"),
            # Grype has testing by version too, without codename
            (FORKY, "debian", "debian", "14", "", "", ""),
        ])
    # how Grype maps Debian testing, e.g. forky
    db.execute("INSERT INTO operating_system_specifier_overrides VALUES "
               "('debian', '', '', 'forky', '', NULL, NULL, NULL, "
               "'unstable', NULL, 1, '')")
    # the Debian security tracker's record of a CVE in openssl
    db.execute("INSERT INTO packages (id, ecosystem, name) "
               "VALUES (1, 'deb', 'openssl')")
    db.execute("INSERT INTO vulnerability_handles (id, name, status, "
               "provider_id) VALUES (1, 'CVE-2025-15467', 'active', "
               "'debian')")
    db.execute("INSERT INTO affected_package_handles (vulnerability_id, "
               "operating_system_id, package_id) VALUES (1, ?, 1)",
               (TRIXIE,))
    db.commit()
    db.close()
    return path


def advisory(advisory_id="QLI-2026-1", ecosystem="Debian:13",
             source="userspace-resource-manager", events=None, **extra):
    doc = {
        "id": advisory_id,
        "published": "2026-09-30T00:00:00Z",
        "modified": "2026-10-01T12:00:00Z",
        "summary": "urm: out-of-bounds write",
        "database_specific": {"severity": "High"},
        "affected": [{
            "package": {"ecosystem": ecosystem, "name": source},
            "ranges": [{"type": "ECOSYSTEM", "events": events or [
                {"introduced": "0"}, {"fixed": "0.4.7-0qli1~bpo13+1"}]}],
        }],
    }
    doc.update(extra)
    return doc


def write_advisories(tmp_path, *docs):
    root = tmp_path / "tracker"
    (root / "advisories").mkdir(parents=True, exist_ok=True)
    paths = []
    for doc in docs:
        path = root / "advisories" / f"{doc['id']}.osv.json"
        path.write_text(json.dumps(doc))
        paths.append(str(path))
    return str(root), paths


def run(db, *args):
    distro_info = os.path.join(os.path.dirname(db), "debian.csv")
    return subprocess.run([sys.executable, SCRIPT, "--distro-info",
                           distro_info, db, *args],
                          capture_output=True, text=True, check=False)


def query(db, sql, *params):
    with sqlite3.connect(db) as conn:
        return conn.execute(sql, params).fetchall()


def handles(db, name):
    # (operating system ID, package name, ranges) of a vulnerability's
    # affected package handles
    return sorted(
        (os_id, package, json.loads(blob)["ranges"])
        for os_id, package, blob in query(
            db, "SELECT a.operating_system_id, p.name, b.value "
                "FROM affected_package_handles a "
                "JOIN vulnerability_handles v ON v.id = a.vulnerability_id "
                "JOIN packages p ON p.id = a.package_id "
                "JOIN blobs b ON b.id = a.blob_id WHERE v.name = ?", name))


def test_adds_advisory(tmp_path):
    db = make_db(tmp_path)
    root, paths = write_advisories(tmp_path, advisory(
        "QLI-CVE-2026-12345", aliases=["CVE-2026-12345"],
        details="More details.",
        severity=[{"type": "CVSS_V3", "score":
                   "CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H"}],
        references=[{"type": "WEB", "url": "https://example.com/urm"}]))
    result = run(db, "--source-root", root, "--source-url",
                 "https://example.com/blob/abc/", *paths)
    assert result.returncode == 0, result.stderr
    assert "QLI-CVE-2026-12345: userspace-resource-manager on debian 13 " \
        "(trixie)" in result.stdout

    (name, status, published, modified, provider, blob), = query(
        db, "SELECT v.name, v.status, v.published_date, v.modified_date, "
            "v.provider_id, b.value FROM vulnerability_handles v "
            "JOIN blobs b ON b.id = v.blob_id WHERE v.provider_id = 'qli'")
    assert (name, status, provider) == \
        ("QLI-CVE-2026-12345", "active", "qli")
    assert published == "2026-09-30 00:00:00+00:00"
    assert modified == "2026-10-01 12:00:00+00:00"
    assert json.loads(blob) == {
        "id": "QLI-CVE-2026-12345",
        "description": "urm: out-of-bounds write\n\nMore details.",
        "refs": [
            {"url": "https://example.com/blob/abc/advisories/"
                    "QLI-CVE-2026-12345.osv.json", "tags": ["ADVISORY"]},
            {"url": "https://example.com/urm", "tags": ["WEB"]},
        ],
        "aliases": ["CVE-2026-12345"],
        # Grype reports the first one
        "severities": [
            {"scheme": "CHMLN", "value": "high", "source": "qli", "rank": 1},
            {"scheme": "CVSS", "value": {
                "vector": "CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H",
                "version": "3.1"}, "source": "qli", "rank": 1},
        ],
    }
    assert query(db, "SELECT * FROM vulnerability_aliases") == \
        [("QLI-CVE-2026-12345", "CVE-2026-12345")]
    assert handles(db, "QLI-CVE-2026-12345") == [
        (TRIXIE, "userspace-resource-manager", [{
            "version": {"type": "dpkg",
                        "constraint": "< 0.4.7-0qli1~bpo13+1"},
            "fix": {"version": "0.4.7-0qli1~bpo13+1", "state": "fixed"},
        }]),
    ]
    (version, processor, digest), = query(
        db, "SELECT version, processor, input_digest FROM providers "
            "WHERE id = 'qli'")
    assert (version, processor) == ("1", "grype-db-add-advisories.py")
    assert digest.startswith("sha256:")


@pytest.mark.parametrize("events,ranges", [
    # like the Debian provider's records of unfixed vulnerabilities
    ([{"introduced": "0"}],
     [{"version": {"type": "dpkg"}, "fix": {"state": "not-fixed"}}]),
    ([{"introduced": "1.0-1"}],
     [{"version": {"type": "dpkg", "constraint": ">= 1.0-1"},
       "fix": {"state": "not-fixed"}}]),
    ([{"introduced": "1.0-1"}, {"fixed": "1.0-3"}, {"introduced": "2.0-1"},
      {"fixed": "2.0-2"}],
     [{"version": {"type": "dpkg", "constraint": ">= 1.0-1, < 1.0-3"},
       "fix": {"version": "1.0-3", "state": "fixed"}},
      {"version": {"type": "dpkg", "constraint": ">= 2.0-1, < 2.0-2"},
       "fix": {"version": "2.0-2", "state": "fixed"}}]),
    # the fixed version isn't known, as for Grype's own OSV records
    ([{"introduced": "0"}, {"last_affected": "1.0-5"}],
     [{"version": {"type": "dpkg", "constraint": "<= 1.0-5"},
       "fix": {"state": "unknown"}}]),
])
def test_ranges(tmp_path, events, ranges):
    db = make_db(tmp_path)
    _, paths = write_advisories(tmp_path, advisory(events=events))
    result = run(db, *paths)
    assert result.returncode == 0, result.stderr
    assert handles(db, "QLI-2026-1") == \
        [(TRIXIE, "userspace-resource-manager", ranges)]


@pytest.mark.parametrize("ecosystem,releases", [
    ("Debian", [BOOKWORM, TRIXIE, UNSTABLE, FORKY]),
    ("Debian:13", [TRIXIE]),
    # Grype matches testing as unstable, and by its version once released
    ("Debian:14", [UNSTABLE, FORKY]),
    ("Debian:sid", [UNSTABLE]),
])
def test_releases(tmp_path, ecosystem, releases):
    db = make_db(tmp_path)
    _, paths = write_advisories(tmp_path, advisory(ecosystem=ecosystem))
    result = run(db, *paths)
    assert result.returncode == 0, result.stderr
    assert [os_id for os_id, _, _ in handles(db, "QLI-2026-1")] == releases


def test_several_entries(tmp_path):
    db = make_db(tmp_path)
    doc = advisory(events=[{"introduced": "0"}])
    doc["affected"].append({
        "package": {"ecosystem": "Debian:13", "name": "testpkg"},
        "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}]}],
    })
    doc["affected"].append({
        "package": {"ecosystem": "Debian", "name": "testpkg"},
        "ranges": [{"type": "ECOSYSTEM", "events": [
            {"introduced": "0"}, {"fixed": "2.0-1"}]}],
    })
    _, paths = write_advisories(tmp_path, doc)
    result = run(db, *paths)
    assert result.returncode == 0, result.stderr
    unfixed = {"version": {"type": "dpkg"}, "fix": {"state": "not-fixed"}}
    fixed = {"version": {"type": "dpkg", "constraint": "< 2.0-1"},
             "fix": {"version": "2.0-1", "state": "fixed"}}
    assert handles(db, "QLI-2026-1") == [
        (BOOKWORM, "testpkg", [fixed]),
        (TRIXIE, "testpkg", [unfixed, fixed]),
        (TRIXIE, "userspace-resource-manager", [unfixed]),
        (UNSTABLE, "testpkg", [fixed]),
        (FORKY, "testpkg", [fixed]),
    ]
    # the existing package row is reused
    assert query(db, "SELECT count(*) FROM packages") == [(3,)]


def test_skips_withdrawn_advisories(tmp_path):
    db = make_db(tmp_path)
    _, paths = write_advisories(tmp_path, {
        "id": "QLI-2026-2", "modified": "2026-10-01T00:00:00Z",
        "withdrawn": "2026-10-01T00:00:00Z"})
    result = run(db, *paths)
    assert result.returncode == 0, result.stderr
    assert "QLI-2026-2: withdrawn" in result.stdout
    assert not handles(db, "QLI-2026-2")
    assert query(db, "SELECT id FROM providers WHERE id = 'qli'") == \
        [("qli",)]


def test_warns_when_debian_tracks_the_cve(tmp_path):
    db = make_db(tmp_path)
    _, paths = write_advisories(tmp_path, advisory(
        "QLI-CVE-2025-15467", aliases=["CVE-2025-15467"],
        ecosystem="Debian", source="openssl"))
    result = run(db, *paths)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("warning:") == 1
    assert "warning: QLI-CVE-2025-15467: CVE-2025-15467 for openssl on " \
        "debian 13 (trixie) is tracked by debian now" in result.stdout


def test_no_advisories(tmp_path):
    db = make_db(tmp_path)
    result = run(db)
    assert result.returncode == 0, result.stderr
    assert query(db, "SELECT id FROM providers WHERE id = 'qli'") == \
        [("qli",)]


@pytest.mark.parametrize("change,message", [
    ({"affected": [{"package": {"ecosystem": "Debian:99",
                                "name": "urm"},
                    "ranges": [{"type": "ECOSYSTEM",
                                "events": [{"introduced": "0"}]}]}]},
     "unknown Debian release '99'"),
    # releases with a version are named by it
    ({"affected": [{"package": {"ecosystem": "Debian:trixie",
                                "name": "urm"},
                    "ranges": [{"type": "ECOSYSTEM",
                                "events": [{"introduced": "0"}]}]}]},
     "unknown Debian release 'trixie'"),
    ({"affected": [{"package": {"ecosystem": "Debian:2.0",
                                "name": "urm"},
                    "ranges": [{"type": "ECOSYSTEM",
                                "events": [{"introduced": "0"}]}]}]},
     "Debian release '2.0' unknown to Grype"),
    ({"affected": [{"package": {"ecosystem": "Ubuntu:24.04",
                                "name": "urm"},
                    "ranges": [{"type": "ECOSYSTEM",
                                "events": [{"introduced": "0"}]}]}]},
     "unsupported ecosystem"),
    ({"affected": [{"package": {"ecosystem": "Debian", "name": "urm"},
                    "ranges": [{"type": "SEMVER",
                                "events": [{"introduced": "0"}]}]}]},
     "only ECOSYSTEM ranges"),
    ({"affected": [{"package": {"ecosystem": "Debian", "name": "urm"},
                    "ranges": [{"type": "ECOSYSTEM",
                                "events": [{"introduced": "0"},
                                           {"limit": "*"}]}]}]},
     "invalid Debian version '*'"),
    ({"affected": [{"package": {"ecosystem": "Debian", "name": "urm"},
                    "ranges": [{"type": "ECOSYSTEM",
                                "events": [{"introduced": "0"},
                                           {"fixed": "1.0:5"}]}]}]},
     "invalid Debian version '1.0:5'"),
    # the first entry is valid, the advisory is still skipped entirely
    ({"affected": [advisory()["affected"][0],
                   {"package": {"ecosystem": "Debian:99", "name": "urm"},
                    "ranges": [{"type": "ECOSYSTEM",
                                "events": [{"introduced": "0"}]}]}]},
     "unknown Debian release '99'"),
    ({"affected": [{**advisory()["affected"][0],
                    "ecosystem_specific": {"binaries": ["liburm1"]}}]},
     "userspace-resource-manager: ecosystem_specific is not supported"),
    ({"affected": []}, "no affected packages"),
    ({"database_specific": {}}, "missing database_specific.severity"),
])
def test_skips_unsupported_advisories(tmp_path, change, message):
    db = make_db(tmp_path)
    _, paths = write_advisories(tmp_path, advisory("QLI-2026-1"),
                                advisory("QLI-2026-2", **change),
                                advisory("QLI-2026-3"))
    result = run(db, *paths)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("warning:") == 1
    assert "QLI-2026-2.osv.json: skipped: " + message in result.stdout
    assert "Traceback" not in result.stderr
    # the others are added, nothing of the skipped one
    assert query(db, "SELECT name FROM vulnerability_handles "
                     "WHERE provider_id = 'qli' ORDER BY name") == \
        [("QLI-2026-1",), ("QLI-2026-3",)]
    assert len(handles(db, "QLI-2026-1")) == 1
    assert len(handles(db, "QLI-2026-3")) == 1
    assert query(db, "SELECT count(*) FROM vulnerability_aliases") == [(0,)]
    assert query(db, "SELECT count(*) FROM affected_package_handles") == \
        [(3,)]


def test_skips_unreadable_advisories(tmp_path):
    db = make_db(tmp_path)
    bad = tmp_path / "QLI-2026-9.osv.json"
    bad.write_text("not JSON")
    _, paths = write_advisories(tmp_path, advisory())
    result = run(db, str(bad), *paths)
    assert result.returncode == 0, result.stderr
    assert "QLI-2026-9.osv.json: skipped" in result.stdout
    assert len(handles(db, "QLI-2026-1")) == 1


def test_rejects_unsupported_databases(tmp_path):
    db = make_db(tmp_path, model=5)
    result = run(db)
    assert result.returncode == 1
    assert "unsupported database schema" in result.stderr

    other = tmp_path / "other"
    other.mkdir()
    db = make_db(other)
    query(db, "ALTER TABLE packages RENAME COLUMN ecosystem TO kind")
    result = run(db)
    assert result.returncode == 1
    assert "packages lacks ecosystem" in result.stderr

    assert run(str(tmp_path / "missing.db")).returncode == 1


def test_needs_distro_info(tmp_path):
    db = make_db(tmp_path)
    _, paths = write_advisories(tmp_path, advisory())
    result = run(db, "--distro-info", str(tmp_path / "missing.csv"), *paths)
    assert result.returncode == 1
    assert "cannot read the Debian releases of distro-info-data" in \
        result.stderr
    assert not query(db, "SELECT id FROM providers WHERE id = 'qli'")


def test_runs_only_once(tmp_path):
    db = make_db(tmp_path)
    assert run(db).returncode == 0
    result = run(db)
    assert result.returncode == 1
    assert "has a qli provider already" in result.stderr
