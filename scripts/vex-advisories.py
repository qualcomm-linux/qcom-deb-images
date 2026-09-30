#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# match our OSV advisories (advisories/*.osv.json) for packages the Debian
# security tracker doesn't cover against the installed packages of a Syft SBOM,
# Grype can't be given extra vulnerability data, so this does
# what Grype does for Debian packages: compare the source version to the
# affected ranges, then apply our OpenVEX statements
#
# warnings are printed to stdout, one per line

import argparse
import json
import sys
import zipfile
from collections import defaultdict
from datetime import datetime, timezone

from debian.debian_support import Version

import vexlib

MATCHER = "qli-advisory-matcher"
DATA_SOURCE = "https://github.com/qualcomm-linux/qcom-deb-images/advisories"

CVSS_METHODS = {
    "CVSS:3.0": "CVSSv3",
    "CVSS:3.1": "CVSSv31",
    "CVSS:4.0": "CVSSv4",
}


def in_ranges(version, entry):
    # OSV's algorithm for ECOSYSTEM ranges: walk the events in version order
    version = Version(version)
    for rng in entry.get("ranges") or []:
        events, limits = vexlib.osv_range_events(rng)
        if limits and not any(limit == "*" or version < Version(limit)
                              for limit in limits):
            continue
        affected = False
        for kind, value in events:
            if kind == "introduced":
                if value == "0" or version >= Version(value):
                    affected = True
            elif kind == "fixed":
                if version >= Version(value):
                    affected = False
            elif version > Version(value):
                affected = False
        if affected:
            return True
    return False


def applies_to_release(entry, releases):
    ecosystem = (entry.get("package") or {}).get("ecosystem") or ""
    if ecosystem == "Debian":
        return True
    return ecosystem.startswith("Debian:") and \
        ecosystem.partition(":")[2] in releases


def fix_versions(version, entries):
    candidates = {value for entry in entries for rng in entry["ranges"]
                  for kind, value in vexlib.osv_range_events(rng)[0]
                  if kind == "fixed" and Version(value) > Version(version)}
    return sorted((value for value in candidates
                   if not any(in_ranges(value, entry) for entry in entries)),
                  key=Version)


def vulnerability_id(advisory):
    # findings are reported under the CVE, like Grype's --by-cve
    if advisory["id"].startswith("QLI-CVE-"):
        return advisory["id"][4:]
    for alias in sorted(advisory.get("aliases") or []):
        if alias.startswith("CVE-"):
            return alias
    return advisory["id"]


def cvss_entries(advisory):
    return [{"version": s["score"].split("/")[0].partition(":")[2],
             "vector": s["score"], "source": "QLI advisory"}
            for s in advisory.get("severity") or []
            if s.get("type", "").startswith("CVSS_V") and
            s.get("score", "").startswith("CVSS:")]


def make_finding(advisory, entries, deb):
    fixes = fix_versions(deb["source_version"], entries)
    vuln_id = vulnerability_id(advisory)
    urls = [r["url"] for r in advisory.get("references") or []
            if r.get("url")]
    related = [{"id": advisory["id"], "namespace": "qli:advisory"}]
    related += [{"id": a, "namespace": "qli:advisory"}
                for a in advisory.get("aliases") or [] if a != vuln_id]
    return {
        "vulnerability": {
            "id": vuln_id,
            "dataSource": DATA_SOURCE,
            "namespace": "qli:advisory",
            "severity": advisory["database_specific"]["severity"],
            "urls": urls,
            "description": advisory.get("details") or
            advisory.get("summary", ""),
            "cvss": cvss_entries(advisory),
            "fix": {"versions": fixes,
                    "state": "fixed" if fixes else "not-fixed"},
            "advisories": [],
        },
        "relatedVulnerabilities": related,
        "matchDetails": [{
            "type": "exact-indirect-match",
            "matcher": MATCHER,
            "searchedBy": {
                "package": {"name": deb["source"],
                            "version": deb["source_version"]},
                "namespace": entry["package"]["ecosystem"],
            },
            "found": {"vulnerabilityID": advisory["id"],
                      "versionConstraint": "see " + advisory["id"]},
        } for entry in entries if in_ranges(deb["source_version"], entry)],
        "artifact": {
            "name": deb["name"],
            "version": deb["version"],
            "type": "deb",
            "purl": deb["purl"],
        },
    }


def apply_vex(finding, documents, product):
    # returns "suppressed", "annotated" or None; mutates the finding
    vuln = finding["vulnerability"]
    ids = [vuln["id"]] + [r["id"] for r in finding["relatedVulnerabilities"]]
    statement = vexlib.find_statement(documents, ids, product,
                                      finding["artifact"]["purl"])
    if statement is None:
        return None
    status = statement["status"]
    if status in vexlib.SUPPRESSING:
        finding["appliedIgnoreRules"] = [{
            "vulnerability": vuln["id"],
            "reason": statement.get("impact_statement") or
            statement.get("status_notes") or "",
            "vex-status": status,
            "vex-justification": statement.get("justification", ""),
        }]
        return "suppressed"
    finding["matchDetails"].append({
        "type": "vex",
        "matcher": "openvex-matcher",
        "found": {"statement": statement},
    })
    return "annotated"


def action_for(finding):
    fixes = finding["vulnerability"]["fix"]["versions"]
    return "Update to " + " or ".join(fixes) if fixes else \
        "No fix available yet"


def make_statements(findings, product, timestamp):
    # Different source packages may need different fixes for the same CVE.
    groups = defaultdict(list)
    for finding in findings:
        groups[(finding["vulnerability"]["id"], action_for(finding))].append(
            finding)
    statements = []
    for (vuln_id, action), group in sorted(groups.items()):
        advisory_id = group[0]["relatedVulnerabilities"][0]["id"]
        statement = {
            "vulnerability": {"name": vuln_id},
            "timestamp": timestamp,
            "products": [{
                "@id": product,
                "subcomponents": [{"@id": purl} for purl in sorted(
                    {f["artifact"]["purl"] for f in group})],
            }],
            "status": "affected",
            "action_statement": action,
            "status_notes": f"Generated from QLI advisory {advisory_id}",
        }
        aliases = sorted({related["id"] for finding in group
                          for related in finding["relatedVulnerabilities"]
                          if related["id"] != vuln_id})
        if aliases:
            statement["vulnerability"]["aliases"] = aliases
        statements.append(statement)
    return statements


def cyclonedx(findings, sbom, timestamp):
    components = {}
    groups = defaultdict(list)
    for finding in findings:
        artifact = finding["artifact"]
        components[artifact["purl"]] = {
            "type": "library", "bom-ref": artifact["purl"],
            "name": artifact["name"], "version": artifact["version"],
            "purl": artifact["purl"],
        }
        groups[finding["vulnerability"]["id"]].append(finding)

    vulns = []
    for vuln_id, group in sorted(groups.items()):
        vuln = group[0]["vulnerability"]
        ratings = [{"source": {"name": "QLI advisory"},
                    "severity": vuln["severity"].lower()}]
        for cvss in vuln["cvss"]:
            ratings.append({
                "source": {"name": "QLI advisory"},
                "method": CVSS_METHODS.get("CVSS:" + cvss["version"], "other"),
                "vector": cvss["vector"],
                "severity": vuln["severity"].lower(),
            })
        states, details = set(), set()
        for finding in group:
            statement = next((detail["found"]["statement"]
                              for detail in finding["matchDetails"]
                              if detail["matcher"] == "openvex-matcher"), {})
            states.add(statement.get("status", "affected"))
            text = statement.get("action_statement") or \
                statement.get("status_notes")
            if text:
                details.add(f"{finding['artifact']['name']}: {text}")
        # CycloneDX analysis applies to every referenced component. Do not
        # mark the whole CVE in_triage while any component remains affected.
        analysis = {
            "state": "in_triage" if states == {"under_investigation"} else
            "exploitable",
        }
        if details:
            analysis["detail"] = "\n".join(sorted(details))
        vulns.append({
            "id": vuln_id,
            "source": {"name": "QLI advisory", "url": DATA_SOURCE},
            "ratings": ratings,
            "description": vuln["description"],
            "advisories": [{"url": u} for u in vuln["urls"]],
            "affects": [{"ref": purl} for purl in sorted(
                {finding["artifact"]["purl"] for finding in group})],
            "analysis": analysis,
            "recommendation": "; ".join(sorted({
                f"{finding['artifact']['name']}: {action_for(finding)}"
                for finding in group})),
        })
    source = sbom.get("source") or {}
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "timestamp": timestamp,
            "tools": {"components": [{
                "type": "application", "name": "vex-advisories.py"}]},
            "component": {"type": "operating-system",
                          "name": source.get("name", ""),
                          "version": source.get("version", "")},
        },
        "components": sorted(components.values(), key=lambda c: c["purl"]),
        "vulnerabilities": vulns,
    }


def grype_reported(grype):
    # Package URLs distinguish installed versions and architectures.
    reported = set()
    for key in ("matches", "ignoredMatches"):
        for item in grype.get(key) or []:
            match = item.get("match", item)
            ids = {match["vulnerability"]["id"]}
            ids.update(r["id"] for r in
                       match.get("relatedVulnerabilities") or [])
            for vuln_id in ids:
                reported.add((vuln_id, match["artifact"].get("purl")))
    return reported


def run(args):
    sbom = vexlib.load_json(args.sbom)
    releases = vexlib.sbom_release(sbom)
    debs = list(vexlib.sbom_debs(sbom))
    product = vexlib.sbom_product(sbom) or vexlib.PRODUCT
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    vex_doc = vexlib.load_json(args.vex) if args.vex else None
    documents = [vex_doc] if vex_doc else []
    reported = grype_reported(vexlib.load_json(args.grype)) \
        if args.grype else set()

    advisories = [vexlib.load_json(path) for path in args.advisories]
    open_findings, ignored, used = [], [], {}
    seen = set()
    sources = {deb["source"] for deb in debs}
    for advisory in advisories:
        if advisory.get("withdrawn"):
            continue
        ids = {advisory["id"], *advisory.get("aliases", [])}
        if ids & seen:
            raise ValueError(f"duplicate advisory IDs/aliases: "
                             f"{', '.join(sorted(ids & seen))}")
        seen.update(ids)
        affected = advisory.get("affected") or []
        if not any(e["package"]["name"] in sources for e in affected):
            print(f"warning: {advisory['id']}: package not in this SBOM")
        for deb in debs:
            entries = [
                entry for entry in affected
                if entry["package"]["name"] == deb["source"] and
                applies_to_release(entry, releases) and
                deb["name"] in (entry.get("ecosystem_specific", {}).get(
                    "binaries", [deb["name"]]))]
            if not any(in_ranges(deb["source_version"], e) for e in entries):
                continue
            if any((vuln_id, deb["purl"]) in reported for vuln_id in ids):
                print(f"warning: {advisory['id']} for {deb['name']} is "
                      "already reported by Grype; remove the advisory "
                      "if Debian tracks it now")
                continue
            finding = make_finding(advisory, entries, deb)
            used[advisory["id"]] = advisory
            if apply_vex(finding, documents, product) == "suppressed":
                ignored.append(finding)
            else:
                open_findings.append(finding)

    if vex_doc is not None:
        untriaged = [f for f in open_findings
                     if not any(m["matcher"] == "openvex-matcher"
                                for m in f["matchDetails"])]
        added = make_statements(untriaged, product, now)
        if added:
            vex_doc.setdefault("statements", []).extend(added)
            vex_doc["timestamp"] = now
            vexlib.write_json(args.vex, vex_doc)
            for finding in untriaged:
                apply_vex(finding, documents, product)

    vexlib.write_json(args.out_json, {
        "matches": open_findings,
        "ignoredMatches": ignored,
        "descriptor": {"name": "vex-advisories.py"},
    })
    vexlib.write_json(args.out_cyclonedx,
                      cyclonedx(open_findings, sbom, now))
    with zipfile.ZipFile(args.out_osv_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for advisory_id in sorted(used):
            z.writestr(f"{advisory_id}.json",
                       json.dumps(used[advisory_id], indent=2) + "\n")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Match QLI OSV advisories against a Syft SBOM.")
    parser.add_argument("advisories", nargs="*", help="*.osv.json files")
    parser.add_argument("--sbom", required=True, help="Syft JSON SBOM")
    parser.add_argument("--grype", help="Grype JSON report; findings it "
                        "already has are dropped, with a warning")
    parser.add_argument("--vex", help="merged OpenVEX document for this "
                        "build: applied to the findings, and updated in "
                        "place with an affected statement for each open "
                        "finding without one")
    parser.add_argument("--out-json", default="rootfs-vulns.advisories.json")
    parser.add_argument("--out-cyclonedx",
                        default="rootfs-vulns.advisories.cyclonedx.json")
    parser.add_argument("--out-osv-zip", default="rootfs-advisories.osv.zip")
    args = parser.parse_args()
    try:
        return run(args)
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    sys.exit(main())
