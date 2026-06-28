#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause
# pylint: disable=invalid-name
"""Check for upgrades of boot binaries and CDT artifacts in YAML files."""

import re
import sys
import argparse
import urllib.request
import hashlib
from functools import partial
from html.parser import HTMLParser

YAML_FILE = "debos-recipes/qualcomm-linux-debian-flash.yaml"

# regex patterns to identify artifact types
# boot binaries use a versioned directory structure: .../rX.Y_.../...
BOOT_BINARIES_PATTERN = re.compile(
    r'(https://softwarecenter\.qualcomm\.com/download/software/chip/'
    r'qualcomm_linux-spf-1-0/qualcomm-linux-spf-1-0_test_device_public)/'
    r'(r[0-9]+\.[0-9]+_[0-9]+\.[0-9]+)/(.*)')
# CDT binaries use a flat file structure in a directory
CDT_PATTERN = re.compile(
    r'(https://artifacts\.codelinaro\.org/artifactory/codelinaro-le)/'
    r'(Qualcomm_Linux/.*)/(cdt)/(.*)')


class MLStripper(HTMLParser):
    """HTML parser to extract links from HTML pages."""

    def __init__(self):
        super().__init__()
        self.reset()
        self.strict = False
        self.convert_charrefs = True
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            for attr in attrs:
                if attr[0] == 'href':
                    self.links.append(attr[1])


def get_links_from_url(url):
    """Fetch URL and extract all links from the HTML page."""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as response:
            html = response.read().decode('utf-8')

        link_parser = MLStripper()
        link_parser.feed(html)
        return link_parser.links
    except (urllib.error.URLError, OSError) as exc:
        print(f"Error fetching links from {url}: {exc}", file=sys.stderr)
        return []


def get_latest_item(base_url, pattern=None, sort_key=None):
    """Get the latest item from a directory listing URL."""
    links = get_links_from_url(base_url)
    items = []
    for link in links:
        # cleanup link
        link = link.rstrip('/')
        if link == '..':
            continue

        if pattern:
            m = re.match(pattern, link)
            if m:
                # store the full link matching the pattern
                items.append(link)
        else:
            # default behavior: collect everything that is not a parent dir
            items.append(link)

    if not items:
        return None

    if sort_key:
        items.sort(key=sort_key)
    else:
        items.sort()

    return items[-1]


def version_sort_key(v):
    """Parse version string (format rX.Y_ZZZZZ.W) for sorting."""
    try:
        # remove 'r'
        parts = v[1:].split('_')
        major = parts[0].split('.')
        minor = parts[1].split('.')
        return [int(x) for x in major + minor]
    except (ValueError, IndexError, AttributeError):
        return v


def compute_sha256(url):
    """Download file from URL and compute its SHA256 checksum."""
    try:
        print(f"  Downloading {url}...")
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as response:
            sha256_hash = hashlib.sha256()
            # Download in chunks to handle large files
            chunk_size = 8192
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                sha256_hash.update(chunk)
        checksum = sha256_hash.hexdigest()
        print(f"  SHA256: {checksum}")
        return checksum
    except (urllib.error.URLError, OSError) as exc:
        print(f"  Error computing checksum for {url}: {exc}",
              file=sys.stderr)
        return None


def check_file(file_path, check_boot_binaries=True, check_cdt=True,
               silicon_families=None, target_boot_rev=None,
               update_checksums=False):
    # pylint: disable=too-many-arguments,too-many-positional-arguments
    # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    """Check file for artifact upgrades and optionally compute checksums."""
    print(f"Checking {file_path}...")
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    for line in lines:
        # Look for "url" "http..."
        url = None
        if '"url"' in line:
            parts = line.split('"url"')
            if len(parts) > 1:
                remaining = parts[1].strip()
                m_url = re.search(r'"(https?://[^"]+)"', remaining)
                if m_url:
                    url = m_url.group(1)

        if not url:
            continue

        # Filter by silicon family if specified
        if silicon_families:
            found_family = False
            for family in silicon_families:
                if family.lower() in url.lower():
                    found_family = True
                    break
            if not found_family:
                continue

        check_strategy = None

        # Determine strategy based on URL pattern
        if check_boot_binaries:
            m_boot = BOOT_BINARIES_PATTERN.match(url)
            if m_boot:
                # Boot Binaries Strategy: Version is a directory in path
                base_url_to_check = m_boot.group(1) + '/'
                current_version = m_boot.group(2)
                suffix = m_boot.group(3)

                def _make_boot_url(base, suf, new_ver):
                    return f"{base}/{new_ver}/{suf}"

                check_strategy = {
                    'name': 'Boot Binaries',
                    'base_url': base_url_to_check,
                    'current_item': current_version,
                    'pattern': r'r[0-9]+\.[0-9]+_[0-9]+\.[0-9]+',
                    'sort_key': version_sort_key,
                    'construct_new_url': partial(
                        _make_boot_url, m_boot.group(1), suffix)
                }

        if check_cdt:
            m_cdt = CDT_PATTERN.match(url)
            if m_cdt:
                # CDT Strategy: Version is part of the filename at the end
                base_part1 = m_cdt.group(1)
                base_part2 = m_cdt.group(2)
                base_part3 = m_cdt.group(3)
                current_filename = m_cdt.group(4)
                directory_url = (
                    f"{base_part1}/{base_part2}/{base_part3}/")

                def _make_cdt_url(dir_url, new_file):
                    return f"{dir_url}{new_file}"

                check_strategy = {
                    'name': 'CDT',
                    'base_url': directory_url,
                    'current_item': current_filename,
                    'pattern': None,  # Match all files
                    'sort_key': None,  # Default sort (alphabetical)
                    'construct_new_url': partial(_make_cdt_url, directory_url)
                }

        if check_strategy:
            print(f"Found {check_strategy['name']} artifact: {url}")
            print(f"  Current: {check_strategy['current_item']}")

            latest_item = None
            if (check_strategy['name'] == 'Boot Binaries' and
                    target_boot_rev):
                latest_item = target_boot_rev
            else:
                latest_item = get_latest_item(
                    check_strategy['base_url'],
                    pattern=check_strategy['pattern'],
                    sort_key=check_strategy['sort_key']
                )

            if latest_item and latest_item != check_strategy['current_item']:
                print(f"  ** New version available: {latest_item} **")
                new_url = check_strategy['construct_new_url'](latest_item)
                print(f"  New URL: {new_url}")
                if update_checksums:
                    compute_sha256(new_url)
            else:
                print("  Up to date.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Check for upgrades of boot binaries and CDT artifacts.")
    parser.add_argument(
        "--boot-binaries", action="store_true",
        help="Check only Boot Binaries artifacts")
    parser.add_argument(
        "--cdt", action="store_true",
        help="Check only CDT artifacts")
    parser.add_argument(
        "--silicons", nargs="+",
        help="List of silicon families to check (e.g. QCS615 QCS9100)")
    parser.add_argument(
        "--boot-binaries-rev",
        help="Target revision for boot binaries (e.g. r1.0_00116.0)")
    parser.add_argument(
        "--update-checksums", action="store_true",
        help="Download updated versions and compute SHA256 checksums")

    args = parser.parse_args()

    # If no specific flags are set, check both
    check_boot = args.boot_binaries
    check_cdt_flag = args.cdt

    if args.silicons:
        check_boot = True

    if not check_boot and not check_cdt_flag:
        check_boot = True
        check_cdt_flag = True

    check_file(
        YAML_FILE, check_boot_binaries=check_boot,
        check_cdt=check_cdt_flag, silicon_families=args.silicons,
        target_boot_rev=args.boot_binaries_rev,
        update_checksums=args.update_checksums)
