# Security

This document describes how security issues in the Qualcomm Linux Debian
images are reported, assessed, fixed and communicated: who is responsible for
each step, which tools and data formats we use, and how Qualcomm's internal
tracking is fed from the public repositories. The images are built from
[qcom-deb-images](https://github.com/qualcomm-linux/qcom-deb-images).

## Reporting a vulnerability

- **Already public** (for example a published CVE): open a
  [GitHub issue](https://github.com/qualcomm-linux/qcom-deb-images/issues/new).
  If you have a patch, send it as a pull request, see
  [CONTRIBUTING.md](https://github.com/qualcomm-linux/qcom-deb-images/blob/main/CONTRIBUTING.md).
- **Not yet public, or urgent**: contact the
  [Product Security team](mailto:product-security@qualcomm.com) or use the
  [Report a Bug](https://www.qualcomm.com/company/product-security/report-a-bug)
  page, with as many details as possible: the version and any example code.
  Do not open a public issue, pull request or discussion for it.

A vulnerability in a Debian package itself is fixed by the Debian Security Team
and the upstream project, so also report it to
[Debian](https://www.debian.org/security/faq#contact). We track its impact on our
images either way.

**Scope**: the images built from `qcom-deb-images`, their build recipes, CI
workflows and scripts, and the QLI packages installed in them. The workflow
below is written for the Debian images. Other distributions built with the
same tooling (for example Ubuntu) follow it with their own vulnerability data
source in place of the Debian Security Tracker.

## Roles and responsibilities

| Role | Responsible for |
| ---- | --------------- |
| Researchers, users, downstream integrators | Responsible disclosure (above). May propose assessments or fixes as pull requests. Can run the same workflow on their own images, see [Using the data downstream](#using-the-data-downstream). |
| Image maintainers of `qcom-deb-images` | Run the SBOM and vulnerability scan in the image builds and keep the tools current. Author and review the OpenVEX assessments on the `security-tracker` branch. Refresh images when fixes land. Report issues to Debian and upstream. Escalate to Product Security. |
| Package maintainers of the `pkg-*` repositories behind the QLI APT repository, including the kernel package | Apply fixes for vulnerabilities in QLI packages and release the fix. Name the CVE or tracking ID (e.g. `QLI-2026-XXXX`, in absence of a CVE) in the `debian/changelog` entry. Give exploitability input for their packages. |
| The Qualcomm Product Security team | Receive and coordinate undisclosed reports with the reporter. Operate QTVMS, Qualcomm's internal source of truth for exploitability analysis, triage and vulnerability tracking. Decide on disclosure and publish security advisories. |
| Qualcomm teams tracking Security CRs in internal tooling | Track the delivery of fixes against their CRs, using only the public data described [below](#public-data-and-the-boundary-to-qualcomm-internal-tracking). |
| Debian Security Team, upstream projects | Fix and announce vulnerabilities in the packages we consume. The Debian Security Tracker is where Grype gets its data. |

## Tools and data formats

| Tool or format | Purpose | Where |
| -------------- | ------- | ----- |
| [Syft](https://github.com/anchore/syft) | SBOM of every rootfs, as CycloneDX, SPDX and Syft JSON. Debian packages only (`--select-catalogers debian`). | `debos.yml` on `main` |
| [Grype](https://github.com/anchore/grype) | Matches the SBOM against the Debian Security Tracker data and applies our OpenVEX assessments. Reports by CVE. Never fails the build: a Debian image can always carry known unfixed CVEs. | `debos.yml` on `main` |
| `grype-vulnerability-summary.py` | Job summary: counts by severity, Critical and High individually, untriaged and suppressed findings. | `scripts/` on `main` |
| [OpenVEX](https://github.com/openvex/spec) | Our exploitability assessments, one document per Debian source package. Read as is by Grype, `vexctl`, etc. | `vex/` on `security-tracker` |
| `vex-check.py` | Validates the documents against the pinned OpenVEX schema and our rules, and checks their coverage of an image SBOM. | `scripts/` on `security-tracker` |
| [OSV](https://ossf.github.io/osv-schema/) **(planned)** | Catalog of vulnerabilities the Debian Security Tracker does not know about, such as those in QLI overlay packages. Prototyped in [#690](https://github.com/qualcomm-linux/qcom-deb-images/pull/690). | OSV on `security-tracker` |
| Provenance tool **(planned)** | Maps a binary package version to its packaging tag, upstream release and cherry-picked commits. See [qli-ci#107](https://github.com/qualcomm-linux/qli-ci/issues/107). | `qli-ci` |
| GitHub issues, pull requests and Actions | Discussion, review and the automation around all of the above. Dependabot keeps Syft and Grype up to date. | `github.com/qualcomm-linux/qcom-deb-images` and `github.com/qualcomm-linux/pkg-*` |
| QTVMS | Qualcomm's third-party vulnerability management system. | Qualcomm internal |
| Orbit, Coral | Qualcomm's CR tracking and workflow automation. | Qualcomm internal |

## Workflow

### 1. Detect

Every image build, including the scheduled daily ones (see
[docs/workflows.md](https://github.com/qualcomm-linux/qcom-deb-images/blob/main/docs/workflows.md)),
generates an SBOM with Syft and scans it with Grype using a Debian vulnerability
database. The reports are uploaded with the image and the summary appears in
the Actions job summary. A new CVE shows up in the first build after the
refreshed database contains it. Triggered and scheduled production builds
deploy their Grype findings to a `latest/` directory in Qualcomm's artifact
storage, to be consumed by QTVMS **(planned)**.

Findings also arrive from reporters, from Debian and upstream, and from
Qualcomm's own deep code scanning of the QLI packages, some of which Debian
does not track. The latter enter QTVMS directly and can be synchronized back
into the `security-tracker` branch's OSV data, once a potential
embargo is lifted **(planned)**.

### 2. Triage

Triage decides whether a finding applies to our images: is the vulnerable code
shipped, reachable and exposed in the way we configure it? Critical and High
findings come first. Debian `wont-fix` findings stay open until someone records
that they do not affect us.

| Outcome | OpenVEX status | Notes |
| ------- | -------------- | ----- |
| Does not apply to our images | `not_affected` with a `justification` and an `impact_statement` | Grype moves the finding to its ignored matches and the summary lists it as suppressed, providing the stated reasoning. |
| Applies, fix or mitigation pending | `affected` with an `action_statement` | The finding stays open and the summary shows the action. |
| Fixed in a version we ship | `fixed`, pinned to the fixed versions | For example a QLI package newer than Debian's. |
| Pending analysis | no statement | Counted as untriaged. |

Image maintainers escalate to Product Security: anything affected at Critical
or High, anything that may be exploited in the wild, any issue in QLI code and
any undisclosed vulnerability. Undisclosed issues are handled privately and
nothing about them goes into public issues, pull requests or the tracker until
they are disclosed and the embargo is lifted.

**Source of truth.** QTVMS is Qualcomm's internal source of truth for exploitability
analysis, triage and CVE tracking. The `security-tracker` branch is the public
mirror of its outcome in open formats, so that downstream consumers can apply
the same assessments. Until QTVMS can export OpenVEX **(planned)**, the
maintainer who merges an assessment also makes sure the decision is recorded in
QTVMS. If the two disagree, QTVMS wins and the tracker is corrected with a
pull request. Assessments proposed from outside are welcome as pull requests
and count as proposals until confirmed.

### 3. Record

Assessments are pull requests against `security-tracker`, one file per Debian
source package, `vex/<source>.openvex.json`. CI validates every document and
checks the whole database for conflicts before it can merge. See
[vex/README.md](vex/README.md) for the rules and a minimal example. Eventually,
we'll run a scheduled import job, extracting triaged vulnerability data from
the QTVMS API and exporting it to the OpenVEX format that can be consumed by
Grype **(planned)**.

Reviewers keep the text of statements free of anything Qualcomm internal, see
[below](#public-data-and-the-boundary-to-qualcomm-internal-tracking).

### 4. Fix

| Origin | Responsible team | How the fix reaches the images |
| ------ | ---------------- | ------------------------------ |
| Debian archive | Debian Security | With a package update. The next build picks it up and Grype stops reporting it. If Debian will not fix a vulnerability that affects us, package maintainers backport the fix in a QLI package or image maintainers mitigate it. QLI maintainers might also send fixes upstream to support the Debian Security team and upstream maintainers. |
| QLI packages (`pkg-*`, including `linux-image-qcom-next`) | Package maintainers | A patched package is released to the QLI APT repository and picked up by the next build. If applicable, the patch is also sent upstream. |
| Image recipes and overlay files in this repository | Image maintainers | A change to `qcom-deb-images`. Overlay files are not packages, so they are not in the SBOM; [#306](https://github.com/qualcomm-linux/qcom-deb-images/issues/306) discusses how to maintain them. |

### 5. Verify and close

The next scheduled build shows the finding gone, or suppressed with its reason.
Assessments pinned to a package version stop matching when that package
changes, so a new version needs a new assessment; the coverage check in the
image build warns about outdated pins and uncovered binaries. A finding is
closed when a build confirms it and the decision is recorded in QTVMS.

### 6. Communicate

| Information | Communicated by | To | Through |
| ----------- | --------------- | -- | ------- |
| Acknowledgement and coordination of a private report, disclosure timing | Product Security | Reporter | Email, private channel |
| Vulnerability status of each build: open, untriaged, suppressed | CI, operated by the image maintainers | QLI maintainers, Product Security | Actions job summary and build artifacts, consumed by QTVMS **(planned)** |
| Exploitability assessments with their justification | Image maintainers | Public | OpenVEX on `security-tracker` branch |
| Security advisories: affected products, fixed versions, remediation **(planned)** | Product Security | Public | QLI security advisories via Qualcomm Security Bulletin **(planned)** |
| Fixes in Debian packages | Debian Security Team | Public | Debian security advisories (DSA), Debian Security Tracker |
| Fixes in QLI packages | Package maintainers | Public | Release tags, the QLI APT repository, `debian/changelog` |
| Vulnerabilities found by Qualcomm in Debian or upstream projects | Image or package maintainers, with Product Security if undisclosed | Debian, upstream | Upstream's preferred vulnerability reporting channel, e.g.: `security@debian.org` or as declared in `SECURITY.md` |
| Delivery status of a fix | Qualcomm Security CR owners | Qualcomm-internal stakeholders | Qualcomm's internal CR tooling (e.g. Orbit), derived from the public data **(planned)** |

## Public data and the boundary to Qualcomm-internal tracking

Everything the internal side needs is published here under public identifiers,
so that the internal tooling can read it without the repositories knowing about
it.

| Data | Where | Identified by |
| ---- | ----- | ------------- |
| SBOM of each image | Build artifacts of the image build | Package URLs (`pkg:deb/debian/<binary>@<version>`), build ID `<run id>-<attempt>`, file prefix `<suite>[-<variant>][-<profile>]` |
| Vulnerability report of each image | Build artifacts and job summary | CVE ID or tracking ID (such as `QLI-2026-XXXX`) |
| Exploitability assessments | `vex/<source>.openvex.json` on `security-tracker` branch | CVE ID/tracking ID and binary package, under the product `pkg:generic/qualcomm-linux-debian-rootfs` |
| QLI package releases and provenance **(planned)** | Release tags in the `pkg-*` repositories, [qli-ci#107](https://github.com/qualcomm-linux/qli-ci/issues/107) | Binary package name and version |
| Advisories **(planned)** | Qualcomm Security Bulletin (e.g. https://qualcomm.com/company/product-security/bulletins) | Advisory ID, CVE ID |

### Tooling workflow
```
  public (GitHub)                                Qualcomm-internal
  ---------------                                -----------------
  image build outputs (SBOM, Grype)  -->
  security-tracker (OpenVEX, OSV)    -->-- read --> QTVMS --> Orbit / Coral
  pkg-* releases (scan & provenance) -->           (triage)   (CR tracking)
                                                      |
  security-tracker (OpenVEX, OSV)  <---- sync/PR -----+
```

Internal systems consume public data, for example from a workflow event, a
webhook or a scheduled poll. QLI maintainers write back to the
`security-tracker` branch with a synchronization job or manually reviewed PRs.
Extension points in public workflows are generic, such as workflow outputs.
What is specific to Qualcomm sits behind them, in internal workflows and
services, and is not part of the public `qualcomm-linux` repositories.

Rules to keep that boundary:

- **Published**: CVEs, tracking- and advisory IDs, package names and versions,
  statuses, justifications, impact and action statements, fixed versions,
  SBOMs, scan results and provenance.
- **Never published**: CR and ticket IDs or URLs, internal priority, deadlines
  and ownership, customer or product-specific impact, internal hostnames and
  credentials, and anything about a vulnerability before its disclosure.
- Internal records point at public identifiers (CVE, source package, build ID,
  tag, commit). Public data never points at internal ones.

## Package provenance for CR tracking (planned)

To tell whether a fix is in a delivered artifact, a CR needs to go from a
binary package version to its packaging release tag, the upstream release it is
based on and the upstream commits cherry-picked into it. The proposal in
[qli-ci#107](https://github.com/qualcomm-linux/qli-ci/issues/107) is:

- A stateless command line tool that takes a binary package name and version
  and prints that mapping as JSON. It derives the answer from Debian metadata
  and the release tags, so the packages carry no extra metadata. Callers cache
  its results.
- A hook in the reusable package release workflow, after the release tag has
  been pushed, to hand the new packages to the CR side.
- A hook in the `qcom-deb-images` builds that does the same for the packages
  listed in the image SBOM.

The tool knows nothing about CRs. Fixes that go into Debian directly are
noticed when the package shows up in an image build.

## Using the data downstream

To apply our assessments to an image of your own, generate the SBOM with the
same source name, since statements are matched on it, and give Grype each
document:

```sh
syft scan rootfs --select-catalogers debian \
    --source-name qualcomm-linux-debian-rootfs --source-version "$BUILD_ID" \
    -o syft-json=rootfs-sbom.syft.json

git clone --depth 1 --branch security-tracker \
    https://github.com/qualcomm-linux/qcom-deb-images.git t
shopt -s globstar nullglob
vex=(); for f in t/vex/**/*.openvex.json; do vex+=(--vex "$f"); done
grype --by-cve "${vex[@]}" sbom:rootfs-sbom.syft.json
```

**Note:** The following command lists the packages of your image that the
assessments do not cover or no longer match:
`t/scripts/vex-check.py --coverage-only --sbom rootfs-sbom.syft.json t/vex/**/*.openvex.json`

## Known gaps

- Only Debian packages in the rootfs tarball are scanned. Packages installed
  later in the image recipe are missed, and so are files that no package owns,
  such as overlay files and the boot binaries used for flashing.
  [#706](https://github.com/qualcomm-linux/qcom-deb-images/issues/706)
- Kernel CVEs dominate the vulnerability findings. Filtering them against the
  shipped kernel `.config` could help with that **(planned)**.
  [#705](https://github.com/qualcomm-linux/qcom-deb-images/issues/705).
- SBOMs and vulnerability reports are uploaded as private artifacts, like the
  images. Only the source files in the `security-tracker` branch are public.
- Vulnerabilities in packages that exist only in the QLI repository are
  invisible to Grype, as Debian does not track them. Until public OSV
  data exist **(planned)**, they are tracked in QTVMS only.
  [#690](https://github.com/qualcomm-linux/qcom-deb-images/pull/690)
- Public security advisories, a combined QTVMS report and QTVMS APIs to
  export/sync data, do not exist yet **(planned)**.
