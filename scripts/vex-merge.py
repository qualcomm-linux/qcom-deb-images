#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# merge our OpenVEX documents (vex/*.openvex.json) into a single document that
# describes one build: the versionless product is replaced by the versioned
# one Grype derives from the SBOM. the result is printed to stdout

import argparse
import copy
import json
import sys
from datetime import datetime, timezone

import vexlib


def merge(documents, product_version, timestamp):
    product = f"{vexlib.PRODUCT}@{product_version}"
    statements = []
    for doc in documents:
        for statement in doc.get("statements") or []:
            statement = copy.deepcopy(statement)
            # a statement without a timestamp inherits the one of its
            # document, which would otherwise be lost
            statement.setdefault("timestamp", doc.get("timestamp"))
            for item in statement.get("products") or []:
                if item.get("@id") == vexlib.PRODUCT:
                    item["@id"] = product
            statements.append(statement)
    statements.sort(key=lambda s: (
        (s.get("vulnerability") or {}).get("name", ""),
        json.dumps(s.get("products"), sort_keys=True)))
    return {
        "@context": vexlib.OPENVEX_CONTEXT,
        "@id": "https://github.com/qualcomm-linux/qcom-deb-images/vex/"
               f"build/{product_version}",
        "author": "Qualcomm Linux Debian images maintainers",
        "timestamp": timestamp,
        "version": 1,
        "statements": statements,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Merge OpenVEX documents into one for a build.")
    parser.add_argument("files", nargs="*", help="*.openvex.json files")
    parser.add_argument("--product-version", required=True,
                        help="version of the product, i.e. the Syft "
                             "--source-version (the build ID)")
    parser.add_argument("--timestamp",
                        help="RFC 3339 timestamp of the merged document "
                             "(default: now)")
    args = parser.parse_args()

    timestamp = args.timestamp or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    documents = [vexlib.load_json(path) for path in args.files]
    json.dump(merge(documents, args.product_version, timestamp),
              sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
