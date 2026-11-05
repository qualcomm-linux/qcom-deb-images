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
# For each kernel source package of the SBOM, but Debian's own (source package
# linux, which the Debian security tracker covers already), this
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
import csv
import glob
import json
import os
import re
import sys

# the source packages of kernel images, e.g. linux, linux-qcom-next
KERNEL_IMAGE_RE = re.compile(r"linux-image-.*")
# the kernels the Debian security tracker covers: Debian's, whose images are
# built from the linux-signed-<architecture> source packages, e.g.
# linux-image-arm64
DEBIAN_SOURCE_RE = re.compile(r"linux(?:-signed-.*)?")

# the upstream version of a Debian kernel package version: e.g. 7.3.0 of
# 7.3.0+git20261030~gae2606baf50f-0qli1~bpo13+1, 7.3.0 rc4 of
# 7.3.0~rc4+git20261007~gba86f6790835-0qli1~bpo13+1, 6.12.48 of 6.12.48-1
DEB_KERNEL_VERSION_RE = re.compile(
    r"(?:\d+:)?(?P<version>\d+(?:\.\d+)*)(?:[~-]rc(?P<rc>\d+))?(?:[+~-].*)?")
# vulns.git versions: 0, 6.14, 6.6.79, 2.6.32.1, 7.4-rc1 for a fix merged
# for a release candidate, and 6.6.* or * as upper bounds of a stable branch
# or of all versions
CVE_VERSION_RE = re.compile(r"(?P<version>\d+(?:\.\d+)*)(?:-rc(?P<rc>\d+))?")
RELEASE = float("inf")
# from distro-info-data
DISTRO_INFO = "/usr/share/distro-info/debian.csv"


class KernelVersion:
    def __init__(self, numbers, rc=None):
        self.numbers = tuple(numbers)
        self.rc = rc

    def key(self, length=None):
        numbers = self.numbers[:length] if length else self.numbers
        numbers = numbers + (0,) * (4 - len(numbers))
        return numbers + (RELEASE if self.rc is None else self.rc,)

    def __str__(self):
        version = ".".join(map(str, self.numbers))
        return f"{version}-rc{self.rc}" if self.rc is not None else version


def deb_kernel_version(version):
    match = DEB_KERNEL_VERSION_RE.fullmatch(version)
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
            yield json.load(f)


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


def sbom_kernels(sbom):
    # {source package: (Debian version, [binary packages])} of the kernel
    # image packages and every other binary package of their source package
    debs = [a for a in sbom.get("artifacts", []) if a.get("type") == "deb"]
    sources = {}
    for artifact in debs:
        metadata = artifact.get("metadata") or {}
        source = metadata.get("source") or artifact["name"]
        if KERNEL_IMAGE_RE.fullmatch(artifact["name"]):
            sources.setdefault(source, artifact["version"])
    kernels = {}
    for source, version in sources.items():
        binaries = sorted({a["name"] for a in debs
                           if ((a.get("metadata") or {}).get("source")
                               or a["name"]) == source
                           and a["version"] == version})
        kernels[source] = (version, binaries)
    return kernels


def sbom_release(sbom, distro_info):
    # the Debian release of an SBOM, as the OSV Debian ecosystem names it,
    # like scripts/grype-db-add-advisories.py: by version, e.g. 13, or by
    # series for those without one, e.g. sid. Testing has no version in its
    # os-release yet, e.g. forky, which distro-info-data knows as 14 though
    distro = sbom.get("distro") or {}
    if distro.get("id") != "debian":
        raise ValueError("not a Debian SBOM")
    version = (distro.get("versionID") or "").split(".")[0]
    if version:
        return version
    series = distro.get("versionCodename")
    with open(distro_info, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["series"] == series and row["version"]:
                return row["version"]
    return series


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
    kernels = {source: kernel for source, kernel in sbom_kernels(sbom).items()
               if not DEBIAN_SOURCE_RE.fullmatch(source)}
    if not kernels:
        # e.g. an image with Debian's kernel; nothing to report
        print("no kernel to assess", file=sys.stderr)
        return
    release = sbom_release(sbom, args.distro_info)

    records = list(load_cves(args.vulns))
    for source, (deb_version, binaries) in sorted(kernels.items()):
        assess(args, records, release, source, deb_version, binaries)


def main():
    parser = argparse.ArgumentParser(
        description="Assess the Linux kernel CNA's CVEs against the kernel "
                    "packages of an image.")
    parser.add_argument("--vulns", required=True,
                        help="checkout of the kernel CNA's vulns.git")
    parser.add_argument("--sbom", required=True,
                        help="Syft JSON SBOM of the image")
    parser.add_argument("--osv-dir",
                        help="directory to write OSV records of the CVEs "
                             "affecting the kernel to, in a <source>_"
                             "<version> subdirectory per kernel, for "
                             "scripts/grype-db-add-advisories.py")
    parser.add_argument("--distro-info", default=DISTRO_INFO,
                        help="Debian releases of distro-info-data, to name "
                             "the release of the SBOM if it has no version "
                             "(default: %(default)s)")
    args = parser.parse_args()
    try:
        run(args)
    except (OSError, KeyError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
