#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# Assess the kernel CVEs of the Linux kernel CNA against the kernel packages
# of an image, from their version; see
# https://github.com/qualcomm-linux/qcom-deb-images/issues/705.
#
# Grype doesn't report any CVE for our kernel packages: their source packages,
# e.g. linux-qcom-next, aren't in the Debian security tracker, and the Debian
# kernel's data wouldn't apply to them anyway. The kernel CNA publishes its
# CVE records in https://git.kernel.org/pub/scm/linux/security/vulns.git
# instead, with the mainline and stable versions they are fixed in, and the
# files their fixes change ("programFiles").
#
# The kernels of an image are the packages of the SBOM installing a kernel
# image, whatever their name: /boot/vmlinuz-*, or /usr/lib/modules/*/vmlinuz
# like Debian's current ones, which leave /boot/vmlinuz-* to their
# maintainer scripts; along with the other binary packages of their source
# package, e.g. linux-image-7.3.0+qcom-next-... along with
# linux-headers-7.3.0+qcom-next-... and linux-image-qcom-next. Kernels from a
# Debian archive, per the APT origins of the image (--apt-origins, from
# scripts/syft-apt-origins.py), are left out: the Debian security tracker
# covers them already, and Grype reports their CVEs from its data, for the
# binary packages of the source package linux, e.g. linux-modules-*; those of
# linux-signed-*, e.g. the signed kernel image, get none. So are the images
# without APT origins, built before they were recorded; an image without any
# kernel is an error. Kernels from no known archive, e.g. the bindeb-pkg
# builds the linux.yml images get from a local APT repository, are assessed
# like those from the QLI archive.
#
# For each kernel, this
# - reads the CVE records of vulns.git and keeps those which affect the
#   upstream version of the package, e.g. 7.3.0 for 7.3.0+git20261030~g...;
# - writes them to --osv-dir, as OSV records for that exact package version,
#   which scripts/grype-db-add-advisories.py adds to Grype's database, so that
#   Grype reports them for every binary package of the source package, as
#   not fixed: no version of the package is known to fix them.
#
# The version check is conservative: release candidates and git snapshots
# count as before their release, so a fix merged in 7.3-rc2 still counts as
# missing in 7.3.0-rc4 snapshots, and backports outside of stable releases
# are not known.

import argparse
import glob
import json
import os
import re
import sys

import apt_pkg
from distro_info import DebianDistroInfo

# the kernel images the kernel packages install, e.g.
# /boot/vmlinuz-7.3.0+qcom-next-20261030-gae2606baf50f or
# /usr/lib/modules/7.2.12+deb13-arm64/vmlinuz
KERNEL_IMAGE_RE = re.compile(
    r"/boot/vmlinuz-[^/]+|(?:/usr)?/lib/modules/[^/]+/vmlinuz")
KERNEL_IMAGES = "/boot/vmlinuz-* or /usr/lib/modules/*/vmlinuz"
# the vendor of the archives of the kernels the Debian security tracker
# covers, see scripts/syft-apt-origins.py
DEBIAN_VENDOR = "Debian"
# the products of the CVE records of the kernel CNA; another one is an error,
# so that it gets reviewed rather than its CVEs silently ignored
KNOWN_PRODUCTS = {"Linux"}

# the kernel version of the upstream version of a Debian kernel package, as
# apt_pkg.upstream_version() gives it: e.g. 7.3.0 of
# 7.3.0+git20261030~gae2606baf50f, 7.3.0 rc4 of
# 7.3.0~rc4+git20261007~gba86f6790835, 6.12.48 of 6.12.48
DEB_KERNEL_VERSION_RE = re.compile(
    r"(?P<version>\d+(?:\.\d+)*)(?:[~-]rc(?P<rc>\d+))?(?:[+~-].*)?")
# vulns.git versions: 0, 6.14, 6.6.79, 2.6.32.1, 7.4-rc1 for a fix merged
# for a release candidate, and 6.6.* or * as upper bounds of a stable branch
# or of all versions
CVE_VERSION_RE = re.compile(r"(?P<version>\d+(?:\.\d+)*)(?:-rc(?P<rc>\d+))?")
RELEASE = float("inf")


class KernelVersion:
    def __init__(self, numbers, rc=None):
        self.numbers = tuple(numbers)
        self.rc = rc

    def key(self, length=None):
        """A tuple comparing like the version, of its first length numbers.

        The numbers are padded to four, and end with the release candidate,
        infinite for a release, so that a release candidate compares before
        its release: 7.3-rc4 is (7, 3, 0, 0, 4), 7.3 and 7.3.0 are
        (7, 3, 0, 0, inf), 7.3.5 is (7, 3, 5, 0, inf).
        """
        numbers = self.numbers[:length] if length else self.numbers
        numbers = numbers + (0,) * (4 - len(numbers))
        return numbers + (RELEASE if self.rc is None else self.rc,)

    def __str__(self):
        version = ".".join(map(str, self.numbers))
        return f"{version}-rc{self.rc}" if self.rc is not None else version


def upstream_version(version):
    # the upstream version of a Debian package version, without epoch and
    # revision; apt's version system must be set up first, or it crashes
    if not upstream_version.ready:
        apt_pkg.init_config()
        apt_pkg.init_system()
        upstream_version.ready = True
    return apt_pkg.upstream_version(version)


upstream_version.ready = False


def deb_kernel_version(version):
    match = DEB_KERNEL_VERSION_RE.fullmatch(upstream_version(version))
    if not match:
        raise ValueError(f"not a kernel package version: {version}")
    rc = int(match["rc"]) if match["rc"] else None
    return KernelVersion(map(int, match["version"].split(".")), rc)


def cve_version(value):
    match = CVE_VERSION_RE.fullmatch(value)
    if not match:
        raise ValueError(f"unsupported version {value!r}")
    rc = int(match["rc"]) if match["rc"] else None
    return KernelVersion(map(int, match["version"].split(".")), rc)


def at_most(version, bound):
    # version <= bound, where bound may end in a wildcard
    if bound == "*":
        return True
    if bound.endswith(".*"):
        prefix = cve_version(bound[:-2])
        length = len(prefix.numbers)
        return version.key(length)[:length] <= prefix.key(length)[:length]
    return version.key() <= cve_version(bound).key()


def version_status(versions, default, version):
    # the status of a version per the semver ranges of a vulns.git record:
    # that of the first range containing it, of an exact entry, or the
    # default; the git commit ranges are left out, they'd need the history of
    # the kernel tree
    for entry in versions:
        if entry.get("versionType") == "git":
            return None
    for entry in versions:
        if "lessThan" not in entry and "lessThanOrEqual" not in entry:
            continue
        start = cve_version(entry["version"]).key()
        if version.key() < start:
            continue
        if "lessThan" in entry:
            if version.key() < cve_version(entry["lessThan"]).key():
                return entry["status"]
        elif at_most(version, entry["lessThanOrEqual"]):
            return entry["status"]
    for entry in versions:
        if "lessThan" not in entry and "lessThanOrEqual" not in entry and \
                cve_version(entry["version"]).key() == version.key():
            return entry["status"]
    return default


def load_cves(vulns_dir):
    pattern = os.path.join(vulns_dir, "cve", "published", "*", "CVE-*.json")
    paths = sorted(glob.glob(pattern))
    if not paths:
        raise ValueError(f"no CVE records in {vulns_dir}")
    for path in paths:
        with open(path, encoding="utf-8") as f:
            record = json.load(f)
        products = {affected.get("product") for affected in
                    record["containers"]["cna"].get("affected", [])}
        if products - KNOWN_PRODUCTS:
            raise ValueError(
                f"{os.path.basename(path)}: unknown product(s) "
                f"{', '.join(sorted(map(str, products - KNOWN_PRODUCTS)))}, "
                "review and add them to KNOWN_PRODUCTS")
        yield record


def cve_affects(record, version):
    statuses = set()
    for affected in record["containers"]["cna"].get("affected", []):
        if affected.get("product") != "Linux":
            continue
        try:
            status = version_status(affected.get("versions", []),
                                    affected.get("defaultStatus", "unknown"),
                                    version)
        except ValueError as error:
            # e.g. a new form of version: rather than failing the scan of
            # every image, count the record as affecting the kernel
            print(f"warning: {record['cveMetadata']['cveId']}: {error}, "
                  f"counted as affecting {version}")
            status = "affected"
        if status is not None:
            statuses.add(status)
    # unaffected only when the record says so; "unknown" counts as affected
    return bool(statuses) and statuses != {"unaffected"}


def severity(record):
    for metric in record["containers"]["cna"].get("metrics", []):
        for key, value in metric.items():
            if key.startswith("cvssV") and value.get("baseSeverity"):
                return value["baseSeverity"].lower(), value["vectorString"]
    return "unknown", None


def osv_record(record, source, release, deb_version):
    cve_id = record["cveMetadata"]["cveId"]
    cna = record["containers"]["cna"]
    description = next((d["value"] for d in cna.get("descriptions", [])
                        if d.get("lang") == "en"), "")
    level, vector = severity(record)
    advisory = {
        "schema_version": "1.7.4",
        "id": cve_id,
        "published": record["cveMetadata"].get("datePublished"),
        "modified": record["cveMetadata"].get("dateUpdated"),
        "summary": description.split("\n\n", 2)[1].strip()
        if description.count("\n\n") >= 1 else "",
        "details": description,
        "affected": [{
            "package": {"ecosystem": f"Debian:{release}", "name": source},
            # exactly the version assessed, which no package version is known
            # to fix yet
            "versions": [deb_version],
        }],
        "references": [{"type": "ADVISORY",
                        "url": f"https://www.cve.org/CVERecord?id={cve_id}"}]
        + [{"type": "FIX", "url": ref["url"]}
           for ref in cna.get("references", []) if ref.get("url")],
        "database_specific": {"severity": level},
    }
    if vector:
        advisory["severity"] = [{"type": "CVSS_V3", "score": vector}]
    return {key: value for key, value in advisory.items() if value}


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


def load_vendors(path):
    # {(package, version): {vendors of the archives providing it}}, from
    # scripts/syft-apt-origins.py
    with open(path, encoding="utf-8") as f:
        origins = json.load(f)
    return {(row["package"], row["version"]):
            {archive["vendor"] for archive in row["archives"]}
            for row in origins["packages"]}


def sbom_release(sbom):
    # the Debian release of an SBOM, as the OSV Debian ecosystem names it,
    # like scripts/grype-db-add-advisories.py: by version, e.g. 13, or by
    # series for those without one, e.g. sid. Testing has no version in its
    # os-release yet, e.g. forky, which python3-distro-info knows as 14 though
    distro = sbom.get("distro") or {}
    if distro.get("id") != "debian":
        raise ValueError("not a Debian SBOM")
    version = (distro.get("versionID") or "").split(".")[0]
    series = distro.get("versionCodename")
    return version or DebianDistroInfo().version(series) or series


def assess(args, records, release, source, deb_version, binaries):
    # the CVEs of a kernel
    version = deb_kernel_version(deb_version)
    affected = [r for r in records if cve_affects(r, version)]
    open_severities = {}
    for record in affected:
        cve_id = record["cveMetadata"]["cveId"]
        if args.osv_dir:
            path = os.path.join(args.osv_dir, f"{source}_{deb_version}",
                                f"{cve_id}.osv.json")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(osv_record(record, source, release, deb_version),
                          f, indent=2)
                f.write("\n")
        level = severity(record)[0]
        open_severities[level] = open_severities.get(level, 0) + 1
    print(f"{source} {deb_version} (kernel {version}, "
          f"{', '.join(binaries)}):")
    print(f"  {len(records)} kernel CVEs, {len(affected)} affect the "
          "version")
    print(f"  {len(affected)} open: " + ", ".join(
        f"{count} {level}" for level, count in sorted(
            open_severities.items(), key=lambda item: -item[1])))


def run(args):
    with open(args.sbom, encoding="utf-8") as f:
        sbom = json.load(f)
    kernels = sbom_kernels(sbom)
    if not kernels:
        raise ValueError(f"no kernel in {args.sbom}: no package installs "
                         f"{KERNEL_IMAGES}")
    if not os.path.exists(args.apt_origins):
        # the SBOM of an image built before they were recorded
        print(f"warning: no APT origins of the image, {args.apt_origins}; "
              "not assessing its kernel CVEs")
        return
    vendors = load_vendors(args.apt_origins)
    for (source, version), (image_packages, _) in list(kernels.items()):
        kernel_vendors = set().union(*(vendors.get((package, version), set())
                                       for package in image_packages))
        if kernel_vendors == {DEBIAN_VENDOR}:
            del kernels[(source, version)]
        elif not kernel_vendors:
            print(f"warning: {source} {version}: from no known APT archive, "
                  "assessing it like a QLI kernel")
    if not kernels:
        # Debian's kernel, which the Debian security tracker covers
        print("no non-Debian kernel to assess", file=sys.stderr)
        return
    release = sbom_release(sbom)

    records = list(load_cves(args.vulns))
    for (source, deb_version), (_, binaries) in sorted(kernels.items()):
        assess(args, records, release, source, deb_version, binaries)


def main():
    parser = argparse.ArgumentParser(
        description="Assess the Linux kernel CNA's CVEs against the kernel "
                    "packages of an image.")
    parser.add_argument("--vulns", required=True,
                        help="checkout of the kernel CNA's vulns.git")
    parser.add_argument("--sbom", required=True,
                        help="Syft JSON SBOM of the image")
    parser.add_argument("--apt-origins", required=True,
                        help="APT origins of the packages of the image, from "
                             "scripts/syft-apt-origins.py")
    parser.add_argument("--osv-dir",
                        help="directory to write OSV records of the CVEs "
                             "affecting the kernel to, in a <source>_"
                             "<version> subdirectory per kernel, for "
                             "scripts/grype-db-add-advisories.py")
    args = parser.parse_args()
    try:
        run(args)
    except (OSError, KeyError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
