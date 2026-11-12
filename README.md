# Qualcomm Linux security tracker

This orphan branch holds the OpenVEX assessments for Qualcomm Linux Debian
images, the OSV advisories for vulnerabilities in packages the Debian security
tracker doesn't cover, their validation tooling, and the CI that checks them.
It has no image build history or image recipes. See
[vex/README.md](vex/README.md) and [advisories/README.md](advisories/README.md)
for the data formats and rules.

## Contributing assessments

Create a topic branch from `security-tracker` and target `security-tracker`
when opening a pull request. Keep assessments under `vex/`, named
`<source>.openvex.json`, and advisories under `advisories/`, named
`<id>.osv.json`. CI validates every such file, including subdirectories,
and checks for conflicts across the entire database.

Follow the repository's [contribution and attribution rules](https://github.com/qualcomm-linux/qcom-deb-images/blob/main/CONTRIBUTING.md).

Install `make`, `curl`, `ca-certificates`, `distro-info-data`,
`python3-debian`, `python3-jsonschema`, and `python3-pytest`, then run:

```sh
make check test
```

The Makefile downloads the upstream OpenVEX and OSV schemas, pinned by commit
and SHA-256 checksum, into `.cache/`. After the download, validation and tests
work offline.

## Consumption by image builds

The `qcom-deb-images` builds generate an SBOM of every image and scan them,
once per workflow run, in the `sbom-compliance-scan.yml` workflow; see
[docs/workflows.md](https://github.com/qualcomm-linux/qcom-deb-images/blob/main/docs/workflows.md#sbom-compliance-scan).
It fetches `vex/` and `advisories/` from this security-tracker. Pull requests
targeting `security-tracker` validate the data before merging, so the scan
trusts the tracker's data. Schema fetching and static validation stay on this
branch.

The scan adds the advisories to Grype's vulnerability database before
scanning, so their findings are part of the regular Grype reports, and passes
the documents in `vex/` to Grype; see
[advisories/README.md](advisories/README.md) and [vex/README.md](vex/README.md).
For images with a non-Debian kernel, it also passes the VEX document of the
kernel CVE assessment it generates for that image, which is not stored here.

The scan also fetches `scripts/vex-check.py` and `scripts/vexlib.py` and runs
coverage checks against the SBOM of each image:

```sh
scripts/vex-check.py --coverage-only --sbom /path/to/rootfs-sbom.syft.json vex/*.openvex.json
```

They report missing binaries and outdated version pins for that particular
image. Coverage-only mode trusts the documents and needs only Python's
standard library, without the schema or `jsonschema`. Warnings appear in the
scan's job log and summary, and in the image's `<prefix>-vulns.tar.gz`
reports, without failing the build. Omit `--coverage-only` to run static
validation along with coverage checks locally.
