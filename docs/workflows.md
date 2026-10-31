# Workflows

This page describes when the scheduled workflows run and the constraints that
led to those times. All times are UTC unless stated. The `cron:` lines in the
workflows deliberately carry no comments; update this page in the same commit
as any change to them.

## Schedule

| Time  | Days    | Workflow                       | File                       |
|-------|---------|--------------------------------|----------------------------|
| 19:00 | Sunday  | EFS Maintenance                | `efs-maintenance.yml`      |
| 21:00 | daily   | Build (qcom-next QLI images)   | `build.yml`                |
| 23:00 | daily   | qcom-7.2 linux build           | `linux-qcom-7.2.yml`       |
| 01:00 | daily   | arduino linux build            | `linux-arduino.yml`        |
| 03:00 | daily   | qcom-laptops linux build       | `linux-qcom-laptops.yml`   |
| 05:00 | daily   | linux-next linux build         | `linux-next.yml`           |
| 07:00 | daily   | Build Debian                   | `build-debian.yml`         |
| 09:00 | daily   | mainline linux build           | `linux-mainline.yml`       |
| 11:00 | daily   | glymur laptops linux build     | `linux-glymur-laptops.yml` |
| 11:00 | Monday  | Build U-Boot for RB1           | `build-u-boot-rb1.yml`     |

`stale-issues.yaml` also runs daily at 01:30, but only on a GitHub-hosted
runner and without LAVA jobs, so it is not part of the sequence above.
`linux-qcom-next.yml` is deprecated and is only manually triggered.

## Why the schedule looks like this

* The QLI images are preferred. The `qcom-next` and `qcom-7.2` images and their
  test results are shared with the test teams, who expect them around 8:00am IST
  (2:30am UTC). The LAVA tests in this repository run fully-featured test cases
  and can take a long time, so these builds start the evening before.
* Builds start two hours apart, on the hour. They share the self-hosted
  runners and the LAVA queue; spacing them out stops each workflow's LAVA jobs
  from queuing behind the previous workflows.
* The rest follow in no particular order. The project-specific kernels
  (arduino, qcom-laptops) come next, then linux-next and Vanilla Debian, which
  tracks upstream Debian rather than what QLI ships. Mainline follows, then the
  short-lived glymur-laptops build, which is the last daily build.
* The weekly U-Boot build runs on Monday after the daily ones. It only starts
  once the daily builds are done with the runners and LAVA, so it doesn't add
  its jobs on top. It also builds one image with the new U-Boot and submits a
  single LAVA boot job on the RB1; U-Boot is only published, from `main`, once
  that boots.
* EFS maintenance runs before the first build, to prune old artifacts, which is
  best avoided while a build is publishing to EFS.


## Changing the schedule

* Keep the `qcom-next` and `qcom-7.2` results landing before the 2:30am UTC
  deadline.
* Keep the two hour gap between builds and don't overlap EFS maintenance.
* Update the table above.

## SBOM compliance scan

`debos.yml` generates the SBOMs of each image it builds and packs them, with
the license summary, into a `<prefix>-sbom.tar.gz` archive, which is published
next to the images and uploaded as an artifact of the same name.
`sbom-compliance-scan.yml` then checks the VEX coverage of all the SBOMs of a
workflow run and scans them for vulnerabilities with Grype, in a single job,
including the QLI advisories, see below.
It packs the reports and the VEX coverage warnings of each image into a
`<prefix>-vulns.tar.gz` archive, which is published next to the images too and
uploaded in a `vulns` artifact. It installs the Grype vulnerability database
through a cache on EFS, see `scripts/grype-db-cache.sh`.

Every workflow building images runs it after its build jobs, even when some
builds failed. The test jobs don't wait for it, and its failures don't fail the
build jobs; they do fail the workflow run though, so `test-pr.yml`, which only
tests the images of successful "Build on PR" runs, skips them.

It can also be run manually with the ID of an earlier workflow run, to rescan
the SBOMs of that run, e.g. after updating the VEX data; the reports are then
published under the manual run.

### QLI advisories

Grype only knows the vulnerabilities of the data sources of its database, which
for Debian packages is the Debian security-tracker, so it can't report those
of packages the tracker doesn't cover, e.g. from the QLI overlay. These are
recorded as OSV advisories in `advisories/` on the QLI security-tracker instead,
see its `advisories/README.md`.

Grype can't load extra vulnerability data, so before scanning,
`sbom-compliance-scan.yml` adds the advisories to the database installed for
the job (not to the cache on EFS), as another Debian data source, provider
`qli`, with `scripts/grype-db-add-advisories.py`. Grype then matches them like
the Debian security-tracker's data and reports their findings in its regular
reports, under the `qli:distro:debian:<release>` namespace, with the advisory,
at the commit of the QLI security-tracker used, as their data source; the VEX
statements apply to them too. Warnings, e.g. about advisories the Debian
security-tracker covers by now, are listed once at the end of the job summary.

### Latest compliance data

The SBOMs and vulnerability reports of the most recent build of each image are
also published at a static location of the S3 bucket, for tooling to fetch them
regularly: `qualcomm-linux/qcom-deb-images/latest/<channel>/<prefix>/`.

`<prefix>` is the prefix of the image's artifacts, `<suite>[-<variant>][-<profile>]`
(e.g. `trixie`, `trixie-xfce-debug`), and `<channel>` is passed to
`sbom-compliance-scan.yml` by the workflow building the image:

* `debian-qli`: the QLI images, from `build.yml`
* `debian-baseline`: the vanilla Debian images, from `build-debian.yml`

The other workflows don't publish their data there.

Each image directory holds the files of its `<prefix>-sbom.tar.gz` and
`<prefix>-vulns.tar.gz` archives, each compressed on its own with gzip:

* `rootfs-sbom.cyclonedx.json.gz`, `rootfs-sbom.spdx.json.gz`,
  `rootfs-sbom.syft.json.gz`, `rootfs-sbom.syft.txt.gz`: the SBOMs, from Syft
* `rootfs-sbom.syft-license-summary.csv.txt.gz`: the license summary
* `rootfs-vulns.grype.json.gz`, `rootfs-vulns.grype.cyclonedx.json.gz`,
  `rootfs-vulns.grype.txt.gz`: the Grype vulnerability reports, including the
  findings from the QLI advisories
* `rootfs-vex-warnings.txt.gz`: the VEX coverage warnings

along with an uncompressed `manifest.json` giving the build the data describes
(`build.id`, `build.run_url`, `build.artifacts_url` where its images are), the
scan that produced the reports (`scan.run_url`, `scan.date`, the Grype version
and database build date) and the SHA-256 of every file. S3 has no symlinks, so
the archives aren't copied there: `archives.sbom.url` and `archives.vulns.url`
point at them in the directories of the build and scan runs, with their
SHA-256 too. The files are overwritten in place, and the manifest last, so
tooling should check the files against the manifest and retry on a mismatch.
See `scripts/compliance-manifest.py`.

This data is only published for the main repository, by runs of its `main`
branch or manual runs, like the Grype database cache. Images whose build failed
keep the data of their last successful build. Nothing is deleted, but the
objects are tagged with `retention=30d` for the retention policy of the S3
bucket: the data of an image which is no longer built expires 30 days after it
was last published, while the data of the others is refreshed by every build.
The archives the manifests point at are published under the directories of the
runs, which may expire sooner. A manual rescan only publishes when given a
channel; mind that rescanning an older run replaces the data of newer builds.
