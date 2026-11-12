# Security

> [!IMPORTANT]
> **Draft – not yet in operation.** This document describes a *proposed*
> vulnerability management workflow. It is open for discussion, and the
> processes, roles and tooling it describes are not (all) in place yet.
> Until then, report vulnerabilities as described in
> [Reporting a vulnerability](#reporting-a-vulnerability).

This document describes how security issues in the Qualcomm Linux Debian
images are reported, assessed, fixed and communicated: who is responsible for
each step, which tools and data formats we use, and which data we publish.
The images are built from
[qcom-deb-images](https://github.com/qualcomm-linux/qcom-deb-images).

Our vulnerability data lives in `security-tracker`, currently the
[`security-tracker` branch](https://github.com/qualcomm-linux/qcom-deb-images/tree/security-tracker)
of `qcom-deb-images`. It might move to its own repository, see
[#703](https://github.com/qualcomm-linux/qcom-deb-images/issues/703).

## Reporting a vulnerability

- **Already public** (for example a published CVE): open a
  [GitHub issue](https://github.com/qualcomm-linux/qcom-deb-images/issues/new)
  with a title like `security: CVE-2026-12345: <package>`.
  If you have a patch, send it as a pull request, see
  [CONTRIBUTING.md](https://github.com/qualcomm-linux/qcom-deb-images/blob/main/CONTRIBUTING.md).
- **Not yet public, or urgent**: contact the
  [Product Security team](mailto:product-security@qualcomm.com) or use the
  [Report a Bug](https://www.qualcomm.com/company/product-security/report-a-bug)
  page, with as many details as possible: the version and any example code.
  Do not open a public issue, pull request or discussion for it.

A vulnerability in a package originating from the upstream Debian project is
fixed by the Debian Security Team and the upstream project, so also report it
to [Debian](https://www.debian.org/security/faq#contact). We track its impact
on our images either way.

**Scope**: the images built from `qcom-deb-images`, their build recipes, CI
workflows and scripts, and downstream packages from the
[QLI apt overlay repository](https://deb.debusine.qualcomm.com/qualcomm/qli/)
installed in them. The workflow below is written for the Debian images. Other
distributions built with the same tooling (for example Ubuntu) follow it with
their own vulnerability data source in place of the Debian Security Tracker.

## Roles and responsibilities

| Role | Responsible for |
| ---- | --------------- |
| Researchers, users, downstream integrators | Responsible disclosure (above). May propose assessments or fixes as pull requests. Can run the same workflow on their own images, see [Using the data downstream](#using-the-data-downstream). |
| Image maintainers of `qcom-deb-images` | Run the SBOM generation and the compliance scan of the image builds and keep the tools current. Author and review the OpenVEX assessments & OSV advisories in `security-tracker`. Refresh images when fixes land. Report issues to Debian and upstream. Escalate to Product Security. |
| Package maintainers of the `pkg-*` repositories behind the QLI APT repository, including the kernel package | Apply fixes for vulnerabilities in downstream packages (from the [QLI apt overlay repository](https://deb.debusine.qualcomm.com/qualcomm/qli/)) and release the fix. Name the CVE or tracking ID (e.g. `QLI-2026-XXXX`, in absence of a CVE) in the `debian/changelog` entry. Give exploitability input for their packages. |
| The Qualcomm Product Security team | Receive and coordinate undisclosed reports with the reporter. Make the final exploitability decisions. Decide on disclosure and publish security advisories. Author and update the OSV advisories in `security-tracker` once a vulnerability is disclosed. |
| Other Qualcomm teams | Follow Qualcomm's internal vulnerability management process, using only the public data described [below](#public-data-and-the-boundary-to-qualcomm-internal-processes). |
| Debian Security Team, upstream projects | Fix and announce vulnerabilities in the packages we consume. The Debian Security Tracker is where Grype gets its data for upstream Debian packages. |
| Linux kernel CNA | Assigns and publishes the CVEs of the Linux kernel, with the releases and commits fixing them, in [vulns.git](https://git.kernel.org/pub/scm/linux/security/vulns.git). |

## Tools and data formats

| Tool or format | Purpose | Where |
| -------------- | ------- | ----- |
| [Syft](https://github.com/anchore/syft) | SBOM of every image, as CycloneDX, SPDX and Syft JSON. Debian packages only (`--select-catalogers debian`). | `debos.yml` on `main` |
| `syft-apt-origins.py` | The APT archive (vendor, URI, suite, component) each package of an image comes from, which the SBOMs don't record, and the image's APT sources. | `scripts/compliance/` on `main` |
| [Grype](https://github.com/anchore/grype) | Matches the image SBOMs against the Debian Security Tracker data, our OSV advisories and the kernel CVEs, and applies our OpenVEX assessments. Reports by CVE. Never fails the build: a Debian image can always carry known unfixed CVEs. | `sbom-compliance-scan.yml` on `main` |
| `grype-vulnerability-summary.py` | Job summary: counts by severity, Critical findings individually, untriaged, triaged and suppressed findings. | `scripts/compliance/` on `main` |
| [OpenVEX](https://github.com/openvex/spec) | Our exploitability assessments, one document per Debian source package. Read as is by Grype, `vexctl`, etc. | `vex/` in `security-tracker` |
| [OSV](https://ossf.github.io/osv-schema/) | Our advisories for vulnerabilities the Debian Security Tracker does not know about, such as those in QLI overlay packages, one record per vulnerability. | `advisories/` in `security-tracker` |
| `vex-check.py` | Validates the OpenVEX documents and OSV advisories against the pinned upstream schemas and our rules, and checks coverage of an image SBOM. | `scripts/` in `security-tracker` |
| `grype-db-add-advisories.py` | Adds the OSV advisories and the kernel CVEs to the Grype vulnerability database of the scan, as provider `qli`. | `scripts/compliance/` on `main` |
| `kernel-cves.py`, `kernel-compiled-sources.py` | Assess non-Debian kernels of an image against the kernel CNA's CVE records: the CVEs affecting its upstream version, those whose fix changes no file compiled into it (from the debug info of its `-dbg` package) and those fixed in an upstream release. | `scripts/compliance/` on `main` |
| Provenance tool **(planned)** | Maps a binary package version to its packaging tag, upstream release and cherry-picked commits. See [qli-ci#107](https://github.com/qualcomm-linux/qli-ci/issues/107). | `qli-ci` |
| GitHub issues, pull requests and Actions | Discussion, review and the automation around all of the above. Dependabot keeps Syft and Grype up to date. | `github.com/qualcomm-linux/qcom-deb-images` and `github.com/qualcomm-linux/pkg-*` |

## Workflow

### 1. Detect

Every image build, including the scheduled ones (see
[docs/workflows.md](https://github.com/qualcomm-linux/qcom-deb-images/blob/main/docs/workflows.md#sbom-compliance-scan)),
generates an SBOM with Syft and records the APT archive of each package. The
`sbom-compliance-scan.yml` workflow then scans the SBOMs of all the images
with Grype, using a Debian vulnerability database extended with our OSV
advisories and the kernel CVEs, see below. The SBOMs are packed into a
`<prefix>-sbom.tar.gz` archive and the reports into a `<prefix>-vulns.tar.gz`
archive per image, both published next to the images, and the summary appears
in the Actions job summary. A new CVE shows up in the first build after the
refreshed database contains it. An earlier run can be rescanned manually, for
example after updating the assessments.

The image builds of the `main` branch also publish the SBOMs and reports of
each image as its latest compliance data, at a static location in Qualcomm's
artifact storage: `latest/<channel>/<prefix>/`, where `<channel>` is
`debian-qli` for the QLI images and `debian-baseline` for the vanilla Debian
images, and `<prefix>` is the image (e.g. `trixie` or `trixie-xfce-debug`),
for other vulnerability management tools to consume it.

The Debian Security Tracker only covers Debian's own kernel. The CVEs of the
non-Debian kernels, such as `linux-qcom-next`, come from the Linux kernel CNA's
records in [vulns.git](https://git.kernel.org/pub/scm/linux/security/vulns.git),
which the scan matches against the upstream version of each image's kernel, see
[Kernel CVEs](https://github.com/qualcomm-linux/qcom-deb-images/blob/main/docs/workflows.md#kernel-cves).

Findings also arrive from reporters, from the upstream Debian project and the
independent upstream projects, and from Qualcomm's own deep code scanning of
downstream packages in the [QLI apt overlay repository](https://deb.debusine.qualcomm.com/qualcomm/qli/),
some of which Debian does not track. Findings in the downstream packages are
handled by Product Security first and can be published as OSV advisories in
`security-tracker` once a potential embargo is lifted, see
[advisories/README.md](advisories/README.md). Grype then reports them like
the Debian Security Tracker's findings.

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

The scan triages the kernel CVEs automatically, in a VEX document of each image
that is part of its reports (`rootfs-kernel.openvex.json`): a CVE whose fix
changes no file compiled into the kernel is `not_affected`
(`vulnerable_code_not_present`), and one fixed in an upstream release, is
`affected`, with the releases named in its `action_statement`. Others stay
untriaged.

Image maintainers escalate to Product Security: anything affected at Critical
or High, anything that may be exploited in the wild, any issue in QLI code and
any undisclosed vulnerability. Undisclosed issues are handled privately and
nothing about them goes into public issues, pull requests or the tracker until
they are disclosed and the embargo is lifted.

**Source of truth.** Product Security makes the final exploitability
decisions, following Qualcomm's internal process. `security-tracker` is the
public record of those decisions in open formats, so that downstream consumers
can apply the same assessments. If a published assessment turns out to be
wrong, it is corrected with a pull request. Assessments proposed from outside
are welcome as pull requests and count as proposals until confirmed.

### 3. Record

Assessments are pull requests against `security-tracker`, one file per Debian
source package, `vex/<source>.openvex.json`. Advisories for vulnerabilities the
Debian Security Tracker does not know about are pull requests too, one file per
vulnerability, `advisories/<id>.osv.json`, named after its CVE
(`QLI-CVE-2026-12345`) or a tracking ID (`QLI-2026-12345`) until a CVE is
assigned. CI validates every document and checks the whole database for
conflicts before it can merge. See [vex/README.md](vex/README.md) and
[advisories/README.md](advisories/README.md) for the rules and examples.
Eventually, a scheduled job will export triaged vulnerability data from
Qualcomm's internal tooling to OpenVEX and propose it as pull requests
**(planned)**.

Reviewers keep the text of statements free of anything Qualcomm internal, see
[below](#public-data-and-the-boundary-to-qualcomm-internal-processes).

### 4. Fix

| Origin | Responsible team | How the fix reaches the images |
| ------ | ---------------- | ------------------------------ |
| Debian archive | Debian Security | With a package update. The next build picks it up and Grype stops reporting it. If Debian will not fix a vulnerability that affects us, package maintainers backport the fix in a QLI package or image maintainers mitigate it. QLI maintainers might also send fixes upstream to support the Debian Security team and upstream maintainers. |
| Downstream QLI packages (`pkg-*`) | Package maintainers | A patched package is released to the QLI APT repository and picked up by the next build. Its OSV advisory gets a `fixed` event with that version. If applicable, the patch is also sent upstream. |
| Non-Debian kernels (e.g. `linux-qcom-next`) | Package maintainers | A kernel package based on an upstream release that fixes the CVE, released to the QLI APT repository. The scan only knows the fixes of upstream releases, so a fix backported otherwise needs a VEX statement in `security-tracker`. |
| Image recipes and overlay files in this repository | Image maintainers | A change to `qcom-deb-images`. Overlay files are not packages, so they are not in the SBOM. |

### 5. Verify and close

The next scheduled build shows the finding gone, or suppressed with its reason.
Assessments pinned to a package version stop matching when that package
changes, so a new version needs a new assessment; the coverage check of the
compliance scan warns about outdated pins and uncovered binaries. A finding is
closed when a build confirms it.

### 6. Communicate

| Information | Communicated by | To | Through |
| ----------- | --------------- | -- | ------- |
| Acknowledgement and coordination of a private report, disclosure timing | Product Security | Reporter | Email, private channel |
| Vulnerability status of each build: open, untriaged, triaged, suppressed | CI, operated by the image maintainers | QLI maintainers, Product Security | Actions job summary and the latest compliance data (e.g. `<prefix>-vulns.tar.gz`), consumed by Qualcomm's internal vulnerability management **(planned)** |
| Exploitability assessments with their justification | Image maintainers | Public | OpenVEX in `security-tracker` |
| Disclosed vulnerabilities in packages Debian does not track, with their fixed versions | Product Security | Public | OSV advisories in `security-tracker` |
| Security advisories: affected products, fixed versions, remediation **(planned)** | Product Security | Public | QLI security advisories via Qualcomm Security Bulletin **(planned)** |
| Fixes in upstream Debian packages | Debian Security Team | Public | Debian security advisories (DSA), Debian Security Tracker |
| Fixes in downstream packages (from the [QLI apt overlay repository](https://deb.debusine.qualcomm.com/qualcomm/qli/)) | Package maintainers | Public | Release tags, the QLI APT repository, `debian/changelog` |
| Vulnerabilities found by Qualcomm in Debian or upstream projects | Image or package maintainers, with Product Security if undisclosed | Debian, upstream | Upstream's preferred vulnerability reporting channel, e.g.: `security@debian.org` or as declared in `SECURITY.md` |

## Public data and the boundary to Qualcomm-internal processes

Everything the internal side needs is published here under public identifiers,
so that the internal tooling can read it without the repositories knowing about
it.

| Data | Where | Identified by |
| ---- | ----- | ------------- |
| SBOM and APT origins of each image | `<prefix>-sbom.tar.gz` next to the images, and `latest/<channel>/<prefix>/` | Package URLs (`pkg:deb/debian/<binary>@<version>`), APT archive (vendor, URI, suite, component), build ID `<run id>-<attempt>`, file prefix `<suite>[-<variant>][-<profile>]` |
| Vulnerability report and kernel CVE assessment of each image | `<prefix>-vulns.tar.gz` next to the images, `latest/<channel>/<prefix>/` and the job summary | CVE ID or tracking ID (such as `QLI-2026-XXXX`) |
| Build and scan of the latest compliance data | `manifest.json` in `latest/<channel>/<prefix>/` | Build ID, workflow run URLs, Grype and database versions, SHA-256 of each file |
| Exploitability assessments | `vex/<source>.openvex.json` in `security-tracker` | CVE ID/tracking ID and binary package, under the product `pkg:generic/qualcomm-linux-debian-rootfs` |
| Advisories for packages that the upstream Debian project does not track | `advisories/<id>.osv.json` in `security-tracker` | `QLI-<CVE>` or tracking ID, Debian source package and versions |
| QLI package releases and provenance **(planned)** | Release tags in the `pkg-*` repositories, [qli-ci#107](https://github.com/qualcomm-linux/qli-ci/issues/107) | Binary package name, version and architecture |
| Advisories **(planned)** | Qualcomm Security Bulletin (e.g. https://qualcomm.com/company/product-security/bulletins) | Advisory ID, CVE ID |

### Tooling workflow
```
  public (GitHub)                                   Qualcomm-internal
  ---------------                                   -----------------
  image compliance data (SBOM, Grype) -->
  security-tracker (OpenVEX, OSV)     -->-- read --> vulnerability management
  pkg-* releases (scan & provenance)  -->            (triage)
                                                        |
  security-tracker (OpenVEX, OSV)  <---- sync/PR ---+---+
```

Internal systems consume public data, for example from a workflow event, a
webhook or a scheduled poll. QLI maintainers write back to
`security-tracker` with a synchronization job or manually reviewed PRs.
Extension points in public workflows are generic, such as workflow outputs.
What is specific to Qualcomm sits behind them, in internal workflows and
services, and is not part of the public `qualcomm-linux` repositories.

Rules to keep that boundary:

- **Published**: CVEs, tracking- and advisory IDs, package names and versions,
  statuses, justifications, impact and action statements, fixed versions,
  SBOMs, scan results and provenance.
- **Never published**: internal ticket IDs or URLs, internal priority, deadlines
  and ownership, customer or product-specific impact, internal hostnames and
  credentials, and anything about a vulnerability before its disclosure.
- Internal records point at public identifiers (CVE, source package, build ID,
  tag, commit). Public data never points at internal ones.

## Package provenance (planned)

To tell whether a fix is in a delivered artifact, one needs to go from a
binary package version to its packaging release tag, the upstream release it is
based on and the upstream commits cherry-picked into it. The proposal in
[qli-ci#107](https://github.com/qualcomm-linux/qli-ci/issues/107) is:

- A stateless command line tool that takes a binary package name, version and
  architecture and prints that mapping as JSON. It derives the answer from
  Debian metadata and the release tags, so the packages carry no extra metadata.
  Callers cache its results.
- A hook in the reusable package release workflow, after the release tag has
  been pushed, to publish the mapping of the new packages as a workflow output.
- A hook in the `qcom-deb-images` builds that does the same for the packages
  listed in the image SBOM.

The tool knows nothing about internal tracking, and vulnerability tracking does
not depend on it: it only helps to verify which fixes a delivered package
contains. Fixes that go into Debian directly are noticed when the package shows
up in an image build.

## Using the data downstream

To apply our advisories and assessments to an image of your own, generate the
SBOM with the same source name, since statements are matched on it, add the
advisories to Grype's vulnerability database and give Grype each document
(needs `python3-distro-info`):

```sh
syft scan rootfs --select-catalogers debian \
    --source-name qualcomm-linux-debian-rootfs --source-version "$BUILD_ID" \
    -o syft-json=rootfs-sbom.syft.json

git clone --depth 1 --branch security-tracker \
    https://github.com/qualcomm-linux/qcom-deb-images.git t
git clone --depth 1 https://github.com/qualcomm-linux/qcom-deb-images.git m
shopt -s globstar nullglob

# add the advisories to a copy of the database, then install that copy
grype db update
db="$(grype db status -o json |
    python3 -c 'import json, sys; print(json.load(sys.stdin)["path"])')"
cp "$db" qli-vulnerability.db
m/scripts/compliance/grype-db-add-advisories.py qli-vulnerability.db \
    t/advisories/**/*.osv.json
grype db import qli-vulnerability.db

vex=(); for f in t/vex/**/*.openvex.json; do vex+=(--vex "$f"); done
GRYPE_DB_AUTO_UPDATE=false grype --config m/.github/grype.yaml --by-cve \
    "${vex[@]}" sbom:rootfs-sbom.syft.json
```

`GRYPE_DB_AUTO_UPDATE=false` keeps Grype from replacing the extended database
with a newer upstream one. The kernel CVE assessment of a non-Debian kernel
takes more steps, see `.github/workflows/sbom-compliance-scan.yml` on `main`.

**Note:** The following command lists the packages of your image that the
assessments do not cover or no longer match:
`t/scripts/vex-check.py --coverage-only --sbom rootfs-sbom.syft.json t/vex/**/*.openvex.json`

## Known gaps

- Only Debian packages are scanned. Files that no package owns, such as
  overlay files, and the boot binaries used for flashing, which are not part
  of the disk image, are missed.
- SBOMs and the latest compliance data are uploaded as private artifacts, like
  the images. Only the source files in `security-tracker` are public.
- Vulnerabilities in packages that exist only in the QLI repository are
  invisible to Grype unless an OSV advisory in `security-tracker` records them.
  Undisclosed ones are tracked internally by Product Security only.
- Public security advisories and an automated sync between `security-tracker`
  and Qualcomm's internal tooling do not exist yet **(planned)**.
