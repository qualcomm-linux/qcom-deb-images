#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# input is a directory holding the compliance data of one image, as published
# under latest/<channel>/<prefix>/ by sbom-compliance-scan.yml, along with the
# Syft and Grype JSON reports of that image and its archives as published under
# the build and scan runs; output is a JSON manifest describing where the data
# comes from, listing the files with their checksum and pointing at the
# archives, so that tooling fetching the data can tell which build it describes
# and check that it got a consistent set of files

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

# bump when changing the meaning of existing fields; adding fields is fine
MANIFEST_VERSION = 1


def load_json(path):
    with open(path, "r", encoding="utf-8") as stream:
        return json.load(stream)


def sha256_of_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def list_files(directory):
    files = {}
    for name in sorted(os.listdir(directory)):
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        files[name] = {
            "sha256": sha256_of_file(path),
            "size": os.path.getsize(path),
        }
    return files


def build_info(syft, repository_url):
    # debos.yml passes its BUILD_ID, "<run id>-<run attempt>", to Syft as the
    # source version; it's also the directory the images are published under
    build_id = (syft.get("source") or {}).get("version") or None
    info = {"id": build_id}
    if build_id:
        run_id, _, attempt = build_id.partition("-")
        if run_id.isdigit() and attempt.isdigit():
            info["run_url"] = (f"{repository_url}/actions/runs/{run_id}"
                               f"/attempts/{attempt}")
    return info


def grype_info(grype):
    descriptor = grype.get("descriptor") or {}
    status = (descriptor.get("db") or {}).get("status") or {}
    return {
        "version": descriptor.get("version"),
        "timestamp": descriptor.get("timestamp"),
        "db_schema": status.get("schemaVersion"),
        "db_built": status.get("built"),
    }


def archive_info(path, base_url):
    # the archives are published under their own name in the run directory
    return {
        "url": f"{base_url.rstrip('/')}/{os.path.basename(path)}",
        "sha256": sha256_of_file(path),
        "size": os.path.getsize(path),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate the manifest of published compliance data")
    parser.add_argument("directory",
                        help="directory holding the published files")
    parser.add_argument("--channel", required=True)
    parser.add_argument("--image", required=True,
                        help="ARTIFACT_PREFIX of the image, e.g. trixie-xfce")
    parser.add_argument("--sbom", required=True,
                        help="Syft JSON SBOM of the image")
    parser.add_argument("--grype", required=True,
                        help="Grype JSON report of the image")
    parser.add_argument("--repository-url", required=True,
                        help="e.g. https://github.com/<owner>/<repo>")
    parser.add_argument("--scan-run-url", required=True)
    parser.add_argument("--artifacts-base-url",
                        help="URL the per-run directories are published "
                             "under, to point at the images")
    parser.add_argument("--reports-url",
                        help="URL the vulnerability reports of the scan run "
                             "are published at")
    parser.add_argument("--build-archive", nargs=2, action="append",
                        default=[], metavar=("NAME", "PATH"),
                        help="archive published under the build run, at "
                             "<artifacts base URL>/<build id>/")
    parser.add_argument("--scan-archive", nargs=2, action="append",
                        default=[], metavar=("NAME", "PATH"),
                        help="archive published under the scan run, at the "
                             "reports URL")
    args = parser.parse_args()

    build = build_info(load_json(args.sbom), args.repository_url)
    if args.artifacts_base_url and build["id"]:
        build["artifacts_url"] = (f"{args.artifacts_base_url.rstrip('/')}/"
                                  f"{build['id']}/")

    scan = {
        "run_url": args.scan_run_url,
        "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if args.reports_url:
        scan["reports_url"] = args.reports_url
    scan["grype"] = grype_info(load_json(args.grype))

    archives = {}
    for name, path in args.build_archive:
        if "artifacts_url" not in build:
            parser.error("--build-archive needs --artifacts-base-url and a "
                         "build id in the SBOM")
        archives[name] = archive_info(path, build["artifacts_url"])
    for name, path in args.scan_archive:
        if not args.reports_url:
            parser.error("--scan-archive needs --reports-url")
        archives[name] = archive_info(path, args.reports_url)

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "channel": args.channel,
        "image": args.image,
        "build": build,
        "scan": scan,
        "archives": archives,
        "files": list_files(args.directory),
    }
    json.dump(manifest, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
