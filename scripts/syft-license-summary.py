#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# input is a Syft JSON file as the first argument; output is a
# human-readable summary of source packages and their licenses in CSV
# format

import json
import hashlib
import argparse
import os
from collections import defaultdict


def load_syft_json(file_path):
    with open(file_path, 'r') as f:
        return json.load(f)


def sha256_of_file(path):
    try:
        with open(path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return "unreadable"


def group_by_source_package(data):
    grouped = defaultdict(lambda: {
        "binaries": set(),
        "licenses": set(),
        "copyrights": {},
        "source_version": None
    })
    for artifact in data.get("artifacts", []):
        metadata = artifact.get("metadata", {})
        binary = metadata.get("package", "unknown")
        source = metadata.get("source") or binary
        version = metadata.get("version", "")
        source_version = metadata.get("sourceVersion") or version
        grouped[source]["binaries"].add(binary)
        grouped[source]["source_version"] = source_version
        for lic in artifact.get("licenses", []):
            grouped[source]["licenses"].add(lic.get("value", "unknown"))
        for loc in artifact.get("locations", []):
            path = loc.get("path", "")
            if "copyright" in path:
                grouped[source]["copyrights"][binary] = path
    return grouped


def print_table(grouped, rootfs_path):
    print("source,version,binaries,licenses,copyright_sha256")
    for source, data in grouped.items():
        binaries = " ".join(sorted(data["binaries"]))
        licenses = " ".join(sorted(data["licenses"]))
        version = data["source_version"] or "unknown"
        hashes = set()
        for path in data["copyrights"].values():
            full_path = os.path.join(rootfs_path, path.lstrip('/'))
            hashes.add(sha256_of_file(full_path))
        hash_summary = " ".join(sorted(hashes))
        print(f"{source},{version},{binaries},{licenses},{hash_summary}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
                 description="Summarize Syft license data.")
    parser.add_argument("syft_json", help="Path to the Syft JSON file")
    parser.add_argument("--rootfs", required=True,
                        help="Base path to the root filesystem")
    args = parser.parse_args()

    syft_data = load_syft_json(args.syft_json)
    syft_grouped = group_by_source_package(syft_data)
    print_table(syft_grouped, args.rootfs)
