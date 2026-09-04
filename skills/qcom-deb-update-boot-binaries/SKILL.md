---
name: qcom-deb-update-boot-binaries
description: >-
  Bump an existing board's boot binaries in the debos flash recipe
  (debos-recipes/qualcomm-linux-debian-flash.yaml) to a newer release: change
  the build-id in the boot_binaries_download URL, recompute and update the
  sha256sum from the actual downloaded archive, and summarise what changed in
  the new release. Use when asked to "update boot binaries", "bump <board> boot
  binaries", "update <SoC> bootbinaries to the latest build", or "move <board>
  to build-id NNNNN". NOT for adding a brand-new board to the recipe, nor for
  bumping the pinned qcom-ptool / qcom-dtb-metadata commits — those are separate
  activities.
---

# Update a board's boot binaries

## What this does

Boot-binary archives are versioned by a build-id embedded in the download URL
(e.g. `…/QCS615_bootbinaries.1.0-test-device-public/00123/…`). Updating a board
to a newer release means changing that build-id, re-verifying the archive's
`sha256sum`, and recording what moved. The `sha256sum` is enforced at build time
(`sha256sum --strict -c -`), so it must come from the actual new bytes.

For checksum discipline and commit style see `AGENTS.md`; this skill covers the
boot-binary-specific steps.

## Procedure

### 1. Locate the entry and the current build-id

Find the board's `boot_binaries_download` in the recipe and note the current
build-id segment in the `url` and the current `sha256sum`. Confirm the same
boot-binaries archive isn't shared by other board entries — if it is, they all
move together (update every entry that references it).

### 2. Find the target build-id

Determine the newer build-id (from the request, or by listing the release
directory). If a board is also tracked in
[meta-qcom](https://github.com/qualcomm-linux/meta-qcom), its
`recipes-bsp/firmware-boot/firmware-qcom-boot-<board>_<buildid>.bb` records the
build-id and `SRC_URI[bootbinaries.sha256sum]` meta-qcom uses — a useful
cross-check, but not a substitute for downloading and hashing yourself.

### 3. Download and verify

```bash
url="…/<NEW_BUILDID>/<Archive>.zip"
curl -fSL -o /tmp/boot.zip "$url"
sha256sum /tmp/boot.zip
# sanity-check the archive still has the expected top-level layout:
unzip -l /tmp/boot.zip | head
```

The printed sum is the new `sha256sum`. If meta-qcom recorded a sum for the same
build-id, confirm it matches.

### 4. Edit the entry

Change **both** the build-id in `url` and the `sha256sum` to the new values.
Leave `name` / `filename` / the in-archive layout alone unless the archive
structure actually changed (the `unzip -l` sanity check tells you). Update every
entry sharing this archive.

### 5. Summarise what changed ("Known fixes")

Record, per SoC/board, what the new release brings — a short changelog aids
review and future bisection. Sources, in order of preference:

- release notes accompanying the build,
- the diff in meta-qcom's `firmware-qcom-boot-*` recipe/`.inc` between the old
  and new build-id, if the board is tracked there,
- otherwise state plainly that no changelog was available.

Keep it to the user-visible effect ("fixes USB enumeration on cold boot"), not a
file-list dump.

### 6. Stage and report

Stage the recipe change. Report: board(s) updated, old→new build-id, old→new
`sha256sum` (and whether it matched meta-qcom), and the per-SoC "Known fixes"
summary. Propose a `fix(debos/flash): update <board> boot binaries to
<buildid>` (or `feat(...)` if the bump adds capability) commit message with the
"Known fixes" notes in the body.
