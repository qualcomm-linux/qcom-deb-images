# Advisories for packages Debian doesn't track (OSV)

Vulnerabilities in packages that only exist in the Qualcomm Linux (QLI)
overlay, such as the binaries built from `userspace-resource-manager`, are
unknown to the Debian security tracker and therefore to Grype. Record them
here as [OSV](https://ossf.github.io/osv-schema/) records, one file per
vulnerability named `<id>.osv.json`. Each build matches them against its SBOM
with `scripts/vex-advisories.py`. Findings show up in the job summary and
the published reports next to Grype's.

Rules, enforced by `scripts/vex-check.py`:

- `id` is `QLI-<CVE>` (e.g. `QLI-CVE-2026-12345`, with the CVE in `aliases`),
  or `QLI-<year>-<n>` until a CVE is assigned. Active records must not reuse
  another record's ID or aliases;
- `affected[].package.name` is the Debian **source** package and the
  ecosystem is `Debian:<release>` (`Debian:13`, or the codename for a suite
  without a number, e.g. `Debian:forky`), or `Debian` for all suites;
- an advisory covers all binary packages built from the source package; list
  the affected ones in `affected[].ecosystem_specific.binaries` to limit it;
- versions use Debian ordering in `ECOSYSTEM` ranges. Each event has exactly
  one of `introduced`, `fixed`, `last_affected`, or `limit`. `introduced: "0"`
  means all earlier versions; `limit: "*"` means no upper limit. `fixed` and
  `limit` are exclusive boundaries; `last_affected` is inclusive. Do not mix
  `fixed` and `last_affected` within one range. Leave out `fixed` while no fix
  exists;
- `database_specific.severity` is one of Critical, High, Medium, Low.

When a fix ships, add the `fixed` event and bump `modified`. Don't delete the
advisory: older builds must keep reporting it. Delete it only when Debian
starts tracking the vulnerability (the build warns about that).
For an erroneous advisory, set `withdrawn` to an RFC 3339 timestamp instead:
withdrawn records produce no findings, VEX statements, or bundle entries.

`QLI` is currently a repository-local identifier prefix, not a registered OSV
database prefix. Publication to an external OSV service requires registration
or migration to the specification's `x_` prefix for local databases.

Each installed component is reported once per advisory, even if several
`affected` entries match it. Fix recommendations only include newer versions
outside every applicable affected range. Deduplication against Grype uses all
aliases and the component's full purl, so another architecture or installed
version is not accidentally omitted.

Generated OpenVEX statements retain the SBOM's purls, including their versions
and qualifiers, and group only components sharing the same update advice.
The JSON report and CycloneDX output include those generated assessments.
CycloneDX lists only open findings; its per-vulnerability analysis is
`in_triage` only when every affected component is under investigation.
Otherwise it remains `exploitable`.

CI rejects malformed records before matching and fails if report generation
fails. Missing-package and already-reported findings only produce warnings.

Example, for a vulnerability fixed in `0.4.7-0qli1~bpo13+1`:

```json
{
  "id": "QLI-CVE-2026-12345",
  "aliases": ["CVE-2026-12345"],
  "published": "2026-09-30T00:00:00Z",
  "modified": "2026-09-30T00:00:00Z",
  "summary": "urm: out-of-bounds write when parsing resource configuration",
  "severity": [
    { "type": "CVSS_V3", "score": "CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:H/I:H/A:H" }
  ],
  "database_specific": { "severity": "High" },
  "affected": [
    {
      "package": { "ecosystem": "Debian:13", "name": "userspace-resource-manager" },
      "ranges": [
        {
          "type": "ECOSYSTEM",
          "events": [ { "introduced": "0" }, { "fixed": "0.4.7-0qli1~bpo13+1" } ]
        }
      ]
    }
  ],
  "references": [
    { "type": "ADVISORY", "url": "https://github.com/qualcomm/userspace-resource-manager/security/advisories/GHSA-..." }
  ]
}
```

Whether a vulnerability matters for our image is recorded separately in
[`vex/`](../vex/README.md).
