# Vulnerability assessments (OpenVEX)

One [OpenVEX](https://github.com/openvex/spec) document per Debian *source*
package, named `<source>.openvex.json`, recording whether a vulnerability
reported against the Qualcomm Linux Debian images applies to them. Each build
passes these documents to Grype, which moves suppressed findings to
`ignoredMatches`.

Rules, enforced by `scripts/vex-check.py` (run in CI on every pull request
targeting `security-tracker`).
It validates the documents against the [OpenVEX JSON
schema](https://github.com/openvex/spec/blob/main/openvex_json_schema.json),
which rejects unknown fields such as misspelt keys, then checks:

- the product is `pkg:generic/qualcomm-linux-debian-rootfs`, without a
  version;
- subcomponents are the affected **binary** packages, as
  `pkg:deb/debian/<binary>`, exactly as Debian spells the name and version
  (no qualifiers, subpath or percent-encoding; Grype matches these against
  the SBOM's encoded purls). List every binary built from the source package.
  Pin a version (`@<version>`) only when the assessment depends on it, always
  for every subcomponent of a `fixed` statement. Versions match exactly,
  not as a lower bound: a new package version needs a new assessment;
- `not_affected` needs a `justification` and an `impact_statement`, `affected`
  needs an `action_statement`;
- no overlapping statements for the same vulnerability and binary across
  documents. A versionless subcomponent overlaps every pinned version. To
  change an assessment, edit the statement and bump the document `version`
  and `timestamp`; don't add a conflicting statement;
- Debian `wont-fix` findings stay open until a statement says otherwise.

Grype matches a statement on the vulnerability ID it reports (a CVE, as the
scan uses `--by-cve`) or on one of the statement's `aliases`, never on the
finding's related vulnerabilities: a statement about a related CVE doesn't
cover the finding.

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

Download the pinned upstream schema, then check your changes (needs `make`,
`curl`, `ca-certificates` and `python3-jsonschema`):

```sh
make vex-schema
scripts/vex-check.py vex/*.openvex.json
```

The download verifies the SHA-256 checksum recorded in the Makefile and
stores the schema in `.cache/`, which is ignored by Git. This branch's CI,
`make check` and `make test` fetch it automatically. Once downloaded, validation and
`py.test-3 ci/test_vex.py` work offline; `--schema PATH` lets the checker use
a schema supplied separately.

Tracker CI rejects invalid assessments before image builds can consume them.
Coverage warnings from `--sbom` (missing binaries or outdated pins) remain
non-fatal. Image CI runs this checker with `--coverage-only` using its SBOM;
this skips static validation and needs neither the schema nor `jsonschema`.
The summary lists suppressed findings after the critical/high table, outside
the open totals, with Grype's fix state and the reason they were hidden.

Grype does all the matching, configured by `.github/grype.yaml` on the
image-build branch. It only reports the
`affected` or `under_investigation` statement behind an open finding when
that statement re-adds an ignored one, so the build runs a second scan with
`.github/grype-triage.yaml`, which ignores everything; the summary takes the triage
status of open findings from it.
