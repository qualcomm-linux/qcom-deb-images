# Vulnerability assessments (OpenVEX)

One [OpenVEX](https://github.com/openvex/spec) document per Debian *source*
package, named `<source>.openvex.json`, recording whether a vulnerability
reported against the Qualcomm Linux Debian images applies to them. Each build
merges these documents and passes them to Grype, which moves suppressed
findings to `ignoredMatches`.

Rules, enforced by `scripts/vex-check.py` (run in CI on every pull request):

- the product is `pkg:generic/qualcomm-linux-debian-rootfs`, without a
  version;
- subcomponents are the affected **binary** packages, as
  `pkg:deb/debian/<binary>`. List every binary built from the source package.
  Pin a version (`@<version>`) only when the assessment depends on it, always
  for every subcomponent of a `fixed` statement. Versions match exactly,
  not as a lower bound: a new package version needs a new assessment;
- `not_affected` needs a `justification` and an `impact_statement`, `affected`
  needs an `action_statement`;
- no overlapping statements for the same vulnerability and binary across
  documents. A versionless scope overlaps every pinned version, and
  qualifiers only separate scopes when their values conflict. To change an
  assessment, edit the statement and bump the document `version` and
  `timestamp`; don't add a conflicting statement;
- Debian `wont-fix` findings stay open until a statement says otherwise.

Minimal example:

```json
{
  "@context": "https://openvex.dev/ns/v0.2.0",
  "@id": "https://github.com/qualcomm-linux/qcom-deb-images/vex/openssl",
  "author": "Qualcomm Linux Debian images maintainers",
  "timestamp": "2026-09-30T00:00:00Z",
  "version": 1,
  "statements": [
    {
      "vulnerability": { "name": "CVE-2025-15467" },
      "products": [
        {
          "@id": "pkg:generic/qualcomm-linux-debian-rootfs",
          "subcomponents": [
            { "@id": "pkg:deb/debian/openssl" },
            { "@id": "pkg:deb/debian/libssl3t64" }
          ]
        }
      ],
      "status": "not_affected",
      "justification": "vulnerable_code_not_in_execute_path",
      "impact_statement": "The affected CMS code path is only reached by ..."
    }
  ]
}
```

Check your changes with `scripts/vex-check.py vex/*.openvex.json`.

The build rejects invalid assessments before scanning. Coverage warnings
from `--sbom` (missing binaries or outdated pins) remain non-fatal.
The summary lists suppressed findings after the critical/high table, outside
the open totals. VEX `not_affected` is displayed as `wont-fix`; VEX `fixed`
shows the assessed, installed binary version rather than a possibly unrelated
fix version from Grype's database.

Vulnerabilities in packages Debian doesn't track go in
[`advisories/`](../advisories/README.md) instead; a statement can then name
the advisory's `QLI-` ID as the vulnerability.
