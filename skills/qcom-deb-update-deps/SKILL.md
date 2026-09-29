---
name: qcom-deb-update-deps
description: >-
  Bump the pinned qcom-ptool and/or qcom-dtb-metadata commit(s) in the debos
  flash recipe (debos-recipes/qualcomm-linux-debian-flash.yaml) to a newer or
  target commit, recompute the archive sha256sum, and review the diff and log
  from the current sha to the target sha for backwards-incompatible changes that
  would require updating qcom-deb-images logic or data. Use when asked to
  "update qcom-ptool", "bump qcom-dtb-metadata", "update the flash recipe
  dependencies", "move ptool/metadata to <sha>", or when another skill
  determines a board needs a newer ptool platform or SoC /soc node. NOT for
  adding a board to the recipe, nor for updating an existing board's boot
  binaries — those are separate activities.
---

# Update flash-recipe dependencies (qcom-ptool / qcom-dtb-metadata)

## What this does

The flash recipe downloads `qcom-ptool` and `qcom-dtb-metadata` as GitHub
archive tarballs pinned to exact commits, verified by `sha256sum`. This skill
bumps a pin and — crucially — checks the range from the current commit to the
target for changes that would break the recipe, since these tools supply the
partition layouts (`qcom-ptool`) and FIT `/soc` metadata + build scripts
(`qcom-dtb-metadata`) the recipe depends on.

The pin bump itself is mechanical; the review is the point.

## Procedure

### 1. Identify current pin and target

```bash
grep -nE 'qcom-ptool/archive|qcom-dtb-metadata/archive' \
    debos-recipes/qualcomm-linux-debian-flash.yaml
```

The `<sha>` is in the archive URL (`…/archive/<sha>.tar.gz`). The target is
either given, or the latest on the tool's default branch:

```bash
gh api repos/qualcomm-linux/<tool>/commits/<branch> --jq '.sha'
```

### 2. Review the range for backwards-incompatible changes

This is the mandatory step. Read both the log and the diff between current and
target, looking for anything the recipe relies on:

```bash
gh api "repos/qualcomm-linux/<tool>/compare/<cur_sha>...<target_sha>" \
    --jq '.commits[] | "\(.sha[0:12]) \(.commit.message | split("\n")[0])"'
gh api "repos/qualcomm-linux/<tool>/compare/<cur_sha>...<target_sha>" \
    --jq '.files[] | "\(.status)\t\(.filename)"'
```

Flag as potentially breaking:

- **qcom-ptool:** renamed/removed `platforms/<platform>/` dirs or
  `partitions.conf`; changes to `gen-ptool.sh`'s CLI/args (the recipe calls
  `scripts/gen-ptool.sh` which shells into ptool); changed disk-type or output
  file naming; changed `partitions.conf` schema.
- **qcom-dtb-metadata:** renamed/removed `/soc` node names in
  `qcom-metadata.dts` (the recipe's `soc_id` values must still resolve); changes
  to `build-dtb-image.sh`'s flags (`--soc`, `--prune`, `--multidtb`/combined
  handling) or its output filenames.
- Anything in a `BREAKING CHANGE:`/`!`-marked commit, or renamed top-level files
  the recipe references by path.

For each concern, check whether the recipe or a board entry needs a
corresponding update, and either make that update in the same change or record
it as required follow-up. Cross-check every board's `soc_id` and
`ptool_platforms` still resolve at the target commit.

### 3. Recompute the archive checksum

The pin is verified by `sha256sum`, so the sum must match the target archive's
bytes:

```bash
url="https://github.com/qualcomm-linux/<tool>/archive/<target_sha>.tar.gz"
curl -fSL -o /tmp/dep.tar.gz "$url"
sha256sum /tmp/dep.tar.gz
```

Note: GitHub archive tarballs are generally byte-stable per commit, but always
use the sum you actually computed.

### 4. Edit the pin

Update the archive `url` (the `<sha>` segment) **and** the matching `sha256sum`
in the download action. Apply any recipe/board-data updates the review found.

### 5. Stage and report

Stage the changes. Report: tool, old→new sha, commit count in range, the
backwards-incompatibility review outcome (explicitly "none found" or the list of
concerns and how each was handled), new `sha256sum`, and any board entries
touched. Propose a commit message — `chore(debos/flash): bump <tool> to
<short-sha>` for a clean bump, or `feat`/`fix`/`refactor(debos/flash)!:` if the
bump required recipe or data changes (mark `!` if those are breaking).

## Notes

- If the bump exists solely to support a new board, do the review here first,
  then complete the board entry as part of that work.
- Prefer a standalone dep-bump commit separate from the board/feature that
  motivated it, so the review and the pin change are easy to audit.
