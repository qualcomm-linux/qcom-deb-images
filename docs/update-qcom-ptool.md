# Updating the qcom-ptool reference

The flash recipe (`debos-recipes/qualcomm-linux-debian-flash.yaml`) downloads
[qcom-ptool](https://github.com/qualcomm-linux/qcom-ptool) as a tarball of a
specific commit, pinned by URL and `sha256sum`:

```yaml
  - action: download
    description: Download qcom-ptool
    url: https://github.com/qualcomm-linux/qcom-ptool/archive/<commit>.tar.gz
    name: qcom-ptool
    filename: qcom-ptool.tar.gz
    sha256sum: <sha256 of the tarball>
    unpack: true
```

Pinning a commit rather than a branch keeps builds reproducible, at the cost of
having to bump the reference by hand whenever qcom-ptool gains partition layout
or tooling changes this repository needs. This document describes that bump.

## Procedure

### 1. Note the currently pinned commit

Take it from the `url:` in the flash recipe:

```bash
export ORIGINAL=80e52fcde1cbae7d725692dea98a27cb479c5a2c
```

### 2. Pick the new commit

Update a local clone of qcom-ptool and use its tip:

```bash
cd qcom-ptool
git pull
export UPDATE=$(git rev-parse HEAD)
```

Any commit works, `HEAD` is just the usual choice.

### 3. Compute the new checksum

The recipe pins the checksum of the tarball GitHub generates for that commit,
so download it rather than computing anything from the clone:

```bash
wget https://github.com/qualcomm-linux/qcom-ptool/archive/$UPDATE.tar.gz
sha256sum $UPDATE.tar.gz
```

### 4. Update the recipe

Edit `debos-recipes/qualcomm-linux-debian-flash.yaml` and replace both the
commit in `url:` and the `sha256sum:` of the `Download qcom-ptool` action.

### 5. Generate the changelog for the commit message

The commit message body lists what came in, as a `git shortlog` of the range,
wrapped to fit a 72-column commit message:

```bash
cd qcom-ptool
git shortlog --no-merges -w72,6,8 $ORIGINAL..$UPDATE
```

`-w72,6,8` wraps at 72 columns, indenting the first line of each entry by 6
spaces and any continuation lines by 8, which is `shortlog`'s usual layout at a
width that leaves the commit message unwrapped by other tools.

### 6. Commit

Use the first 7 digits of the new commit in the subject, and paste the
`shortlog` output into the body:

```
feat(debos/flash): update qcom-ptool to commit <7-digit sha from UPDATE>

The following changes were merged in qcom-ptool since <7-digit sha from ORIGINAL>:

Name (count):
      first change
      second change

Other Name (1):
      another change
```

Commit with `--signoff`.

If the update is needed for a specific fix, say so in a sentence above the
changelog; the shortlog on its own rarely explains *why* the bump happens now.

## Updating to a fork

Development sometimes needs a branch that has not landed upstream yet, in which
case the `url:` points at a fork (e.g.
`https://github.com/obbardc/qcom-ptool/archive/<commit>.tar.gz`). The procedure
is unchanged apart from the host in the URL, but such a commit must never reach
`main`: mark it `HACK:` in the subject so it is obvious the reference has to go
back to `qualcomm-linux/qcom-ptool` before merging.
