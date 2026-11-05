#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# Record the APT source the kernel of an image was installed from, for
# scripts/kernel-compiled-sources.py to download its -dbg package from the
# same source, at the vulnerability scan; see docs/workflows.md#kernel-cves.
#
# Given the root filesystem of an image, this looks the installed
# linux-image-* packages up in the image's own APT package lists, with
# python3-apt, and writes the stanzas of the image's
# /etc/apt/sources.list.d/*.sources which they come from, as they are, e.g.
# with their Signed-By key, whatever the image recipe configured: the QLI
# repository, a snapshot of it, or another one. Debian's own kernel, of the
# source packages linux and linux-signed-*, is left out, like in
# scripts/kernel-cves.py, and so are local sources (file:), which aren't
# available at the scan.
#
# Without package lists in the image, it writes all its enabled sources, with
# a warning on stdout, prefixed with "warning:"; without any kernel, or source,
# nothing.

import argparse
import os
import re
import sys

KERNEL_IMAGE_RE = re.compile(r"linux-image-.*")
# the kernels the Debian security tracker covers: Debian's, whose images are
# built from linux-signed-<architecture>; see scripts/kernel-cves.py
DEBIAN_SOURCE_RE = re.compile(r"linux(?:-signed-.*)?")
FIELD_RE = re.compile(r"(?P<field>[A-Za-z0-9-]+):[ \t]*(?P<value>.*)")


def paragraphs(text):
    # the paragraphs of a deb822 file, as they are, and their fields; a
    # Signed-By key inline is a field with continuation lines
    for paragraph in re.split(r"\n[ \t]*\n", text):
        fields, field = {}, None
        for line in paragraph.splitlines():
            if line.startswith("#"):
                continue
            if line[:1] in (" ", "\t"):
                if field:
                    fields[field] += "\n" + line
                continue
            match = FIELD_RE.fullmatch(line)
            if match:
                field = match["field"].lower()
                fields[field] = match["value"]
        if fields:
            yield paragraph.strip("\n") + "\n", fields


def image_sources(rootfs):
    # (stanza, fields) of the enabled deb sources of an image, but local ones
    directory = os.path.join(rootfs, "etc/apt/sources.list.d")
    names = sorted(name for name in os.listdir(directory)
                   if name.endswith(".sources")) \
        if os.path.isdir(directory) else []
    for name in names:
        with open(os.path.join(directory, name), encoding="utf-8") as f:
            for stanza, fields in paragraphs(f.read()):
                if fields.get("enabled", "yes").lower() in ("no", "false"):
                    continue
                if "deb" not in fields.get("types", "").split():
                    continue
                if not [uri for uri in fields.get("uris", "").split()
                        if not uri.startswith("file:")]:
                    continue
                yield stanza, fields


def provides(fields, uri, suite):
    # whether a source stanza is the APT source of that URI and suite
    return suite in fields.get("suites", "").split() and \
        uri.rstrip("/") in (u.rstrip("/") for u in fields.get(
            "uris", "").split())


def installed_kernels(rootfs):
    # (package, version, architecture) of the installed kernel images, but
    # Debian's
    with open(os.path.join(rootfs, "var/lib/dpkg/status"),
              encoding="utf-8") as f:
        status = f.read()
    for _, fields in paragraphs(status):
        package = fields.get("package", "")
        source = fields.get("source", package).split()[0]
        if KERNEL_IMAGE_RE.fullmatch(package) and \
                fields.get("status", "").endswith(" installed") and \
                not DEBIAN_SOURCE_RE.fullmatch(source):
            yield package, fields.get("version"), fields.get("architecture")


def kernel_source_entries(rootfs, kernels):
    # the (URI, suite) of the APT sources which the image's package lists say
    # the kernel packages come from; python3-apt is only needed here
    import apt_pkg
    apt_pkg.init_config()
    apt_pkg.config.set("Dir", rootfs)
    apt_pkg.config.set("Dir::State::status",
                       os.path.join(rootfs, "var/lib/dpkg/status"))
    architectures = sorted({architecture for _, _, architecture in kernels})
    apt_pkg.config.set("APT::Architecture", architectures[0])
    for architecture in architectures:
        apt_pkg.config.set("APT::Architectures::", architecture)
    apt_pkg.init_system()
    cache = apt_pkg.Cache(None)
    source_list = apt_pkg.SourceList()
    source_list.read_main_list()
    # the (URI, suite) of each index of the image's sources
    indexes = {index.describe: (meta.uri, meta.dist)
               for meta in source_list.list for index in meta.index_files}
    entries = set()
    for package, version, _ in kernels:
        if package not in cache:
            continue
        for candidate in cache[package].version_list:
            if candidate.ver_str != version:
                continue
            for package_file, _ in candidate.file_list:
                index = source_list.find_index(package_file)
                if index is not None and index.describe in indexes:
                    entries.add(indexes[index.describe])
    return entries


def main():
    parser = argparse.ArgumentParser(
        description="Record the APT source the kernel of an image was "
                    "installed from.")
    parser.add_argument("rootfs", help="root filesystem of the image")
    parser.add_argument("output", help="sources file to write")
    args = parser.parse_args()
    rootfs = os.path.abspath(args.rootfs)
    try:
        kernels = list(installed_kernels(rootfs))
        if not kernels:
            print("no kernel to record the APT source of", file=sys.stderr)
            return 0
        sources = list(image_sources(rootfs))
        entries = kernel_source_entries(rootfs, kernels)
    except (OSError, SystemError, UnicodeDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    stanzas = [stanza for stanza, fields in sources
               if any(provides(fields, uri, suite) for uri, suite in entries)]
    if not stanzas:
        # e.g. an image without package lists
        print(f"warning: no APT source of {', '.join(k[0] for k in kernels)}"
              " in the package lists of the image; recording all its "
              "sources")
        stanzas = [stanza for stanza, _ in sources]
    if not stanzas:
        print("warning: the image has no APT sources to record")
        return 0
    with open(args.output, "w", encoding="utf-8") as f:
        f.write("\n".join(stanzas))
    for package, version, _ in kernels:
        print(f"{package} {version}: from {len(stanzas)} APT source(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
