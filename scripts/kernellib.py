# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# The kernels of an image, from its Syft SBOM and its APT origins, for
# scripts/kernel-cves.py and scripts/kernel-compiled-sources.py, so that both
# tell them the same way; see docs/workflows.md#kernel-cves.

import re

# the kernel images the kernel packages install, e.g.
# /boot/vmlinuz-7.3.0+qcom-next-20261030-gae2606baf50f or
# /usr/lib/modules/7.2.12+deb13-arm64/vmlinuz
KERNEL_IMAGE_RE = re.compile(
    r"/boot/vmlinuz-[^/]+|(?:/usr)?/lib/modules/[^/]+/vmlinuz")
KERNEL_IMAGES = "/boot/vmlinuz-* or /usr/lib/modules/*/vmlinuz"
# the vendor of the archives of the kernels the Debian security tracker
# covers, see scripts/syft-apt-origins.py
DEBIAN_VENDOR = "Debian"
# of the lists of the files compiled into a kernel, from
# scripts/kernel-compiled-sources.py, <source>_<version> followed by this
COMPILED_SOURCES_SUFFIX = ".compiled-sources.txt"


def deb_source(artifact):
    return (artifact.get("metadata") or {}).get("source") or artifact["name"]


def sbom_kernels(sbom):
    # {(source package, Debian version): ([kernel image packages],
    # [binary packages])} of the packages installing a kernel image, per
    # the files the SBOM records them to contain, and of every other binary
    # package of their source package
    debs = {a["id"]: a for a in sbom.get("artifacts", [])
            if a.get("type") == "deb"}
    files = {f["id"]: f.get("location", {}).get("path", "")
             for f in sbom.get("files", [])}
    images = {debs[r["parent"]]["name"]: debs[r["parent"]]
              for r in sbom.get("artifactRelationships", [])
              if r.get("type") == "contains" and r.get("parent") in debs
              and KERNEL_IMAGE_RE.fullmatch(files.get(r.get("child"), ""))}
    kernels = {}
    for image in images.values():
        key = (deb_source(image), image["version"])
        kernels.setdefault(key, ([], []))[0].append(image["name"])
    for (source, version), (image_packages, binaries) in kernels.items():
        image_packages.sort()
        binaries.extend(sorted({a["name"] for a in debs.values()
                                if deb_source(a) == source
                                and a["version"] == version}))
    return kernels


def non_debian_kernels(kernels, origins):
    # {(source package, Debian version): (architecture, [archives])} of the
    # kernels of sbom_kernels() but those from a Debian archive, which the
    # Debian security tracker covers, per the APT origins of the image, from
    # scripts/syft-apt-origins.py: with the other archives of their kernel
    # image packages, none for a kernel from no known archive, e.g. a
    # bindeb-pkg build from a local APT repository
    rows = {(row["package"], row["version"]): row
            for row in origins["packages"]}
    selected = {}
    for (source, version), (image_packages, _) in kernels.items():
        image_rows = [rows[(package, version)] for package in image_packages
                      if (package, version) in rows]
        archives = []
        for row in image_rows:
            archives.extend(archive for archive in row["archives"]
                            if archive not in archives)
        if archives and all(archive["vendor"] == DEBIAN_VENDOR
                            for archive in archives):
            continue
        architecture = image_rows[0]["architecture"] if image_rows else ""
        selected[(source, version)] = (
            architecture, [archive for archive in archives
                           if archive["vendor"] != DEBIAN_VENDOR])
    return selected
