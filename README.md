# Qualcomm Linux security tracker

This orphan branch holds the OpenVEX assessments for Qualcomm Linux Debian
images, their validation tooling, and the CI that checks them. It has no image
build history or image recipes. See [vex/README.md](vex/README.md) for the data
format and assessment rules.

## Contributing assessments

Create a topic branch from `security-tracker` and target `security-tracker`
when opening a pull request. Keep assessments under `vex/`, named
`<source>.openvex.json`. CI validates every such file, including subdirectories,
and checks for conflicts across the entire database.

Follow the repository's [contribution and attribution rules](https://github.com/qualcomm-linux/qcom-deb-images/blob/main/CONTRIBUTING.md).

Install `make`, `curl`, `ca-certificates`, `python3-jsonschema`, and
`python3-pytest`, then run:

```sh
make check test
```

The Makefile downloads an upstream OpenVEX schema pinned by commit and SHA-256
checksum into `.cache/`. It is not vendored. After the download, validation
and tests work offline.

## Consumption by image builds

The debos workflow fetches `vex/` from this branch's HEAD and passes the
documents to Grype. Pull requests targeting `security-tracker` validate the
data before merging, so image builds trust the branch's data. Schema fetching
and static validation stay on this branch.

The image workflow also fetches `scripts/vex-check.py` and `scripts/vexlib.py`
and runs coverage checks against the generated SBOM:

```sh
scripts/vex-check.py --coverage-only --sbom /path/to/rootfs-sbom.syft.json vex/*.openvex.json
```

They report missing binaries and outdated version pins for that particular
image. Coverage-only mode trusts the documents and needs only Python's
standard library, without the schema or `jsonschema`. Warnings appear in the
image build's job log and summary without failing the build. Omit
`--coverage-only` to run static validation along with coverage checks locally.
