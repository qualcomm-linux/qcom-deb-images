#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# Assess the kernel CVEs of the Linux kernel CNA against the kernel packages
# of an image, from their version and, optionally, the source files compiled
# into them; see https://github.com/qualcomm-linux/qcom-deb-images/issues/705.
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
# syft-apt-origins.py), are left out: the Debian security tracker
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
#   which grype-db-add-advisories.py adds to Grype's database, so that
#   Grype reports them for every binary package of the source package;
# - with --compiled-sources, writes an OpenVEX document to --vex with a
#   not_affected statement for each of those CVEs whose fix changes no source
#   file compiled into the kernel: Grype moves their findings to
#   ignoredMatches, with the justification. kernel-compiled-sources.py
#   lists them from the -dbg package of the kernel.
#
# The CVEs open remain not fixed, as no version of the package is known to fix
# them; the upstream releases fixing them, the stable release of the kernel's
# branch and the mainline one, are at the end of their description, and with
# --vex in an affected statement, whose action names them, for the triage scan
# and grype-vulnerability-summary.py to show.
#
# The version check is conservative: release candidates and git snapshots
# count as before their release, so a fix merged in 7.3-rc2 still counts as
# missing in 7.3.0-rc4 snapshots, and backports outside of stable releases
# are not known. Files other than C, assembly or Rust sources and headers
# count as compiled, e.g. device trees; documentation never does, and Kconfig
# files and Makefiles only when a fix changes nothing else, and only those of
# directories with compiled sources.

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

import apt_pkg
from distro_info import DebianDistroInfo

import kernellib

PRODUCT = "pkg:generic/qualcomm-linux-debian-rootfs"
AUTHOR = "Qualcomm Linux Debian images maintainers"
VEX_ID = "https://github.com/qualcomm-linux/qcom-deb-images/vex/generated/"
CODE_RE = re.compile(r".*\.(c|h|S|rs)")
DOCUMENTATION_PREFIXES = ("Documentation/",)
BUILD_FILE_RE = re.compile(r"(?:.*/)?(?:Kconfig[^/]*|Makefile|Kbuild)")

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


def program_files(record):
    return sorted({name
                   for affected in record["containers"]["cna"].get(
                       "affected", [])
                   for name in affected.get("programFiles", [])})


class CompiledSources:
    # the source files compiled into a kernel
    def __init__(self, files):
        self.files = set(files)
        self.directories = {os.path.dirname(name) for name in self.files}

    def present(self, files):
        # the files of a fix which are (or may be) compiled into the kernel:
        # its code, or else its build files; a Kconfig change for a driver
        # which isn't built doesn't matter, nor one for another architecture
        files = [name for name in files
                 if not name.startswith(DOCUMENTATION_PREFIXES)]
        code = [name for name in files if not BUILD_FILE_RE.fullmatch(name)]
        if code:
            return [name for name in code
                    if not CODE_RE.fullmatch(name) or name in self.files]
        return [name for name in files
                if os.path.dirname(name) in self.directories]


def severity(record):
    for metric in record["containers"]["cna"].get("metrics", []):
        for key, value in metric.items():
            if key.startswith("cvssV") and value.get("baseSeverity"):
                return value["baseSeverity"].lower(), value["vectorString"]
    return "unknown", None


def upstream_fixes(record, version):
    # the upstream releases fixing a CVE: the stable release of the kernel's
    # branch, if any, then the mainline one, e.g. 7.2.5 and 7.3 for 7.2.0
    branch = ".".join(map(str, version.numbers[:2]))
    stable, mainline = [], []
    for affected in record["containers"]["cna"].get("affected", []):
        if affected.get("product") != "Linux":
            continue
        for entry in affected.get("versions", []):
            if entry.get("status") != "unaffected":
                continue
            if entry.get("versionType") == "original_commit_for_fix":
                mainline.append(entry["version"])
            elif entry.get("lessThanOrEqual") == f"{branch}.*":
                stable.append(entry["version"])
    return list(dict.fromkeys(stable + mainline))


def osv_record(record, source, release, deb_version, fixes=()):
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
        "details": "\n\n".join(filter(None, [
            description, fixes and f"Fixed upstream in {', '.join(fixes)}."])),
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


def sbom_release(sbom):
    # the Debian release of an SBOM, as the OSV Debian ecosystem names it,
    # like grype-db-add-advisories.py: by version, e.g. 13, or by
    # series for those without one, e.g. sid. Testing has no version in its
    # os-release yet, e.g. forky, which python3-distro-info knows as 14 though
    distro = sbom.get("distro") or {}
    if distro.get("id") != "debian":
        raise ValueError("not a Debian SBOM")
    version = (distro.get("versionID") or "").split(".")[0]
    series = distro.get("versionCodename")
    return version or DebianDistroInfo().version(series) or series


def vex_products(binaries, deb_version):
    return [{
        "@id": PRODUCT,
        "subcomponents": [{"@id": f"pkg:deb/debian/{name}@{deb_version}"}
                          for name in binaries],
    }]


def affected_statement(cve_id, binaries, deb_version, fixes):
    # a CVE left open which upstream releases fix
    return {
        "vulnerability": {"name": cve_id},
        "products": vex_products(binaries, deb_version),
        "status": "affected",
        "action_statement":
            f"Fixed upstream in {', '.join(fixes)} (generated by "
            "kernel-cves.py).",
    }


def vex_statement(cve_id, binaries, deb_version, files, source):
    return {
        "vulnerability": {"name": cve_id},
        "products": vex_products(binaries, deb_version),
        "status": "not_affected",
        "justification": "vulnerable_code_not_present",
        "impact_statement":
            f"None of the files the fix changes ({', '.join(files)}) are "
            f"compiled into {source} {deb_version}, per the DWARF debug "
            "info of its vmlinux and modules (generated by "
            "kernel-cves.py).",
    }


def load_compiled_sources(path, source, deb_version):
    # the compiled sources of a kernel, from a list or from a directory of
    # lists named after the kernels; None when there is none
    if os.path.isdir(path):
        path = os.path.join(path, f"{source}_{deb_version}"
                            f"{kernellib.COMPILED_SOURCES_SUFFIX}")
        if not os.path.exists(path):
            return None
    with open(path, encoding="utf-8") as f:
        return CompiledSources(line.strip() for line in f if line.strip())


def assess(args, records, release, source, deb_version, binaries):
    # the VEX statements about the CVEs of a kernel
    version = deb_kernel_version(deb_version)
    sources = None
    if args.compiled_sources:
        sources = load_compiled_sources(args.compiled_sources, source,
                                        deb_version)
        if sources is None:
            print(f"warning: no compiled sources for {source} "
                  f"{deb_version}, not checking them")
    affected = [r for r in records if cve_affects(r, version)]
    statements = []
    not_present, fixed_upstream = 0, 0
    open_severities = {}
    for record in affected:
        cve_id = record["cveMetadata"]["cveId"]
        files = program_files(record)
        fixes = upstream_fixes(record, version)
        if args.osv_dir:
            path = os.path.join(args.osv_dir, f"{source}_{deb_version}",
                                f"{cve_id}.osv.json")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(osv_record(record, source, release, deb_version,
                                     fixes), f, indent=2)
                f.write("\n")
        if sources is not None and files and not sources.present(files):
            not_present += 1
            statements.append(vex_statement(cve_id, binaries, deb_version,
                                            files, source))
        else:
            level = severity(record)[0]
            open_severities[level] = open_severities.get(level, 0) + 1
            if fixes:
                fixed_upstream += 1
                statements.append(affected_statement(cve_id, binaries,
                                                     deb_version, fixes))
    print(f"{source} {deb_version} (kernel {version}, "
          f"{', '.join(binaries)}):")
    print(f"  {len(records)} kernel CVEs, {len(affected)} affect the "
          "version")
    if sources is not None:
        print(f"  {not_present} of them change no compiled file: "
              "not_affected")
    print(f"  {len(affected) - not_present} open: " + ", ".join(
        f"{count} {level}" for level, count in sorted(
            open_severities.items(), key=lambda item: -item[1])))
    print(f"  {fixed_upstream} of the open ones fixed upstream: affected")
    return statements


def run(args):
    with open(args.sbom, encoding="utf-8") as f:
        sbom = json.load(f)
    kernels = kernellib.sbom_kernels(sbom)
    if not kernels:
        raise ValueError(f"no kernel in {args.sbom}: no package installs "
                         f"{kernellib.KERNEL_IMAGES}")
    if not os.path.exists(args.apt_origins):
        # the SBOM of an image built before they were recorded
        print(f"warning: no APT origins of the image, {args.apt_origins}; "
              "not assessing its kernel CVEs")
        return
    with open(args.apt_origins, encoding="utf-8") as f:
        selected = kernellib.non_debian_kernels(kernels, json.load(f))
    kernels = {key: kernels[key] for key in selected}
    for (source, version), (_, archives) in sorted(selected.items()):
        if not archives:
            print(f"warning: {source} {version}: from no known APT archive, "
                  "assessing it like a QLI kernel")
    if not kernels:
        # Debian's kernel, which the Debian security tracker covers
        print("no non-Debian kernel to assess", file=sys.stderr)
        return
    release = sbom_release(sbom)

    records = list(load_cves(args.vulns))
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    statements = []
    for (source, deb_version), (_, binaries) in sorted(kernels.items()):
        statements.extend(assess(args, records, release, source, deb_version,
                                 binaries))

    # an OpenVEX document needs statements
    if args.vex and statements:
        document = {
            "@context": "https://openvex.dev/ns/v0.2.0",
            "@id": VEX_ID + "-".join(
                f"{source}-{version}" for source, version in sorted(kernels)),
            "author": AUTHOR,
            "timestamp": timestamp,
            "version": 1,
            "tooling": "kernel-cves.py",
            "statements": statements,
        }
        with open(args.vex, "w", encoding="utf-8") as f:
            json.dump(document, f, indent=2)
            f.write("\n")


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
                             "syft-apt-origins.py")
    parser.add_argument("--compiled-sources",
                        help="list of the source files compiled into the "
                             "kernel, from "
                             "kernel-compiled-sources.py, or a "
                             "directory of them named <source>_<version>"
                             f"{kernellib.COMPILED_SOURCES_SUFFIX}")
    parser.add_argument("--osv-dir",
                        help="directory to write OSV records of the CVEs "
                             "affecting the kernel to, in a <source>_"
                             "<version> subdirectory per kernel, for "
                             "grype-db-add-advisories.py")
    parser.add_argument("--vex",
                        help="OpenVEX document to write the not_affected "
                             "and affected statements to, unless there are "
                             "none")
    args = parser.parse_args()
    try:
        run(args)
    except (OSError, KeyError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
