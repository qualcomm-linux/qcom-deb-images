#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# input is a Syft JSON file as the first argument and the APT lists
# directory of the scanned root filesystem (/var/lib/apt/lists) as the
# second argument; output is a human-readable list of binary packages,
# their source packages, the vendors of the APT archives providing them
# (the first word of their Origin) and these archives in CSV format, e.g.:
#
# package,version,architecture,source,source_version,origins,archives
# acl,2.3.2-2+b1,arm64,acl,2.3.2-2,Debian,Debian/stable
# libaom3,3.12.1-1+deb13u1,arm64,aom,3.12.1-1+deb13u1,Debian,Debian/stable Debian/stable-security  # noqa: E501
# rmtfs,1.3-1~bpo13+1,arm64,rmtfs,1.3-1~bpo13+1,Debian,Debian Backports/stable-backports  # noqa: E501
# libdiag1,1.0.3-0qli2~bpo13+1,arm64,libdiag,1.0.3-0qli2~bpo13+1,Qualcomm,Qualcomm/trixie  # noqa: E501

import os
import re
import sys
import json
import argparse
from collections import defaultdict

RELEASE_FIELDS = re.compile(r"^(Origin|Suite): (.*)$")

# vendors of the archives the images are built from, as found in their
# list file names; the first word of the Origin must match it
VENDORS = ("debian", "qualcomm")


def load_syft_json(file_path):
    with open(file_path, 'r') as f:
        return json.load(f)


# map the list file prefix of each archive, e.g.
# deb.debian.org_debian_dists_trixie_, to "<Origin>/<Suite>" from its
# Release file
def archive_names(lists_dir):
    names = {}
    for name in os.listdir(lists_dir):
        for suffix in ("InRelease", "Release"):
            if not name.endswith("_" + suffix):
                continue
            prefix = name[:-len(suffix)]
            if prefix in names:
                continue
            fields = {}
            with open(os.path.join(lists_dir, name), 'r') as f:
                for line in f:
                    match = RELEASE_FIELDS.match(line.rstrip("\n"))
                    if match and match.group(1) not in fields:
                        fields[match.group(1)] = match.group(2)
            origin = fields.get("Origin", "unknown")
            suite = fields.get("Suite", "unknown")
            check_vendor(name, prefix, origin)
            names[prefix] = f"{origin}/{suite}"
    return names


# fail on archives which aren't known or whose Origin doesn't match the
# vendor of their URL, as their packages would be attributed wrongly
def check_vendor(name, prefix, origin):
    vendors = [v for v in VENDORS if v in prefix]
    if not vendors:
        sys.exit(f"error: {name}: unknown archive vendor, expected one "
                 f"of {', '.join(VENDORS)} in its name")
    if origin.split(" ", 1)[0].lower() != vendors[0]:
        sys.exit(f"error: {name}: Origin {origin!r} doesn't match the "
                 f"vendor {vendors[0]!r} of the archive")


# map each (package, version, architecture) to the archives listing it
def archive_packages(lists_dir, names):
    packages = defaultdict(set)
    for name in os.listdir(lists_dir):
        if not name.endswith("_Packages"):
            continue
        prefixes = [p for p in names if name.startswith(p)]
        if not prefixes:
            continue
        archive = names[max(prefixes, key=len)]
        with open(os.path.join(lists_dir, name), 'r') as f:
            stanza = {}
            for line in f:
                line = line.rstrip("\n")
                if not line:
                    if stanza:
                        key = (stanza.get("Package"), stanza.get("Version"),
                               stanza.get("Architecture"))
                        packages[key].add(archive)
                    stanza = {}
                elif not line[0].isspace() and ":" in line:
                    field, value = line.split(":", 1)
                    stanza[field] = value.strip()
            if stanza:
                key = (stanza.get("Package"), stanza.get("Version"),
                       stanza.get("Architecture"))
                packages[key].add(archive)
    return packages


def package_origins(data, packages):
    rows = []
    for artifact in data.get("artifacts", []):
        metadata = artifact.get("metadata", {})
        binary = metadata.get("package", "unknown")
        version = metadata.get("version", "")
        arch = metadata.get("architecture", "")
        source = metadata.get("source") or binary
        source_version = metadata.get("sourceVersion") or version
        archives = packages.get((binary, version, arch), set())
        rows.append((binary, version, arch, source, source_version,
                     archives))
    return sorted(rows)


def print_table(rows):
    print("package,version,architecture,source,source_version,origins,"
          "archives")
    for binary, version, arch, source, source_version, archives in rows:
        # the vendor of each archive, e.g. Debian for Debian Backports
        origins = {a.split("/", 1)[0].split(" ", 1)[0] for a in archives}
        origin_summary = " ".join(sorted(origins)) or "unknown"
        archive_summary = " ".join(sorted(archives)) or "unknown"
        print(f"{binary},{version},{arch},{source},{source_version},"
              f"{origin_summary},{archive_summary}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
                 description="List the APT archives of Syft packages.")
    parser.add_argument("syft_json", help="Path to the Syft JSON file")
    parser.add_argument("apt_lists",
                        help="Path to the APT lists directory")
    args = parser.parse_args()

    syft_data = load_syft_json(args.syft_json)
    names = archive_names(args.apt_lists)
    packages = archive_packages(args.apt_lists, names)
    rows = package_origins(syft_data, packages)
    # an empty table means the APT lists are missing or don't match
    # the SBOM
    if not any(archives for *_, archives in rows):
        sys.exit("error: no package found in the APT lists")
    print_table(rows)
