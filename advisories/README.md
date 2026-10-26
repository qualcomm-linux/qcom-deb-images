# Advisories for packages Debian doesn't track (OSV)

Vulnerabilities in packages that only exist in the Qualcomm Linux (QLI)
overlay, such as the binaries built from `userspace-resource-manager`, are
unknown to the Debian security tracker and therefore to Grype. Record them
here as [OSV](https://ossf.github.io/osv-schema/) records, one file per
vulnerability, named `<id>.osv.json`.

Before scanning, the `sbom-compliance-scan.yml` workflow adds those records
to its GrypeDB copy. Grype then matches them like the Debian security tracker's
data: the findings are part of the regular Grype reports (JSON, CycloneDX,
table), under a `qli` namespace, and the VEX statements in
[`vex/`](../vex/README.md) apply to them too. Their data source is the
corresponding OSV file on the QLI `security-tracker`.

Local rules are enforced by `scripts/vex-check.py` (run in CI on every pull
request targeting `security-tracker`). It validates the records against the
[OSV JSON schema](https://github.com/ossf/osv-schema/blob/main/validation/schema.json),
which rejects unknown fields such as misspelt keys, except for the `id`, then
checks:

- `id` is `QLI-<CVE>` (e.g. `QLI-CVE-2026-12345`, with the CVE in `aliases`
  and no other CVE there), or `QLI-<year>-<n>` until a CVE is assigned; rename
  the advisory to `QLI-<CVE>` once it is. Grype reports the former as the CVE
  (the scan uses `--by-cve`), the latter as the QLI ID. No two records claim
  the same ID or alias;
- `database_specific.severity` is one of `Critical`, `High`, `Medium`, `Low`;
  it's the severity Grype reports. `severity` may add CVSS vectors
  (`CVSS_V2`, `CVSS_V3`, `CVSS_V4`);
- `affected[].package.name` is the Debian **source** package and the
  ecosystem is `Debian:<release>` as the OSV schema names releases: by their
  version in distro-info-data (e.g. `Debian:13`, or `Debian:14` for forky),
  or by series for those without one (`Debian:sid`); or `Debian` for all
  releases. The image builds translate these into the releases Grype knows,
  e.g. testing is matched as unstable.
- an advisory covers every binary package built from the source package,
  like the Debian security tracker's data: Grype reports it for each of them.
  When only some are affected, record `not_affected` statements in
  [`vex/`](../vex/README.md) for the others, as for any CVE.
- ranges are `ECOSYSTEM` ranges, with Debian versions, and their events are
  pairs of `introduced` and `fixed` (exclusive) or `last_affected`
  (inclusive), in ascending version order; the last `introduced` may be
  left open while no fix exists. `introduced: "0"` means all earlier
  versions. A range can't mix `fixed` and `last_affected`, use separate
  ranges. `limit` and `versions` aren't supported.

When a fix ships, add the `fixed` event and bump `modified`. Don't delete the
advisory: rescans of older builds must keep reporting it. Delete it only when
Debian starts tracking the vulnerability, which the image-build branch warns
about. For an erroneous advisory, set `withdrawn` to an RFC 3339 timestamp
instead: withdrawn records are not added to the database.

`QLI` is a repository-local identifier prefix, not a registered OSV database
prefix, hence the exception for the `id` in the schema validation.
Publication to an external OSV service requires registration or migration to
the specification's `x_` prefix for local databases.

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

Whether a vulnerability matters for our images is recorded separately, in
[`vex/`](../vex/README.md): name the CVE in statements about `QLI-<CVE>`
advisories, and the QLI ID otherwise.

Download the pinned upstream schemas, then check your changes (needs `make`,
`curl`, `ca-certificates`, `distro-info-data`, `python3-debian` and
`python3-jsonschema`):

```sh
make check
```
