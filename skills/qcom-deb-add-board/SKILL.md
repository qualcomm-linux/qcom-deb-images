---
name: qcom-deb-add-board
description: >-
  Add support for a new Qualcomm board to the debos flash recipe
  (debos-recipes/qualcomm-linux-debian-flash.yaml) by appending a board entry
  to the templated board list: pick the DTB name (from the board's kernel repo,
  defaulting to the mainline Linux kernel), the soc_id (matching
  qcom-dtb-metadata's /soc node), the qcom-ptool platform(s), and the
  checksum-verified boot-binaries and CDT downloads. Use when asked to "add a
  board", "add support for <board>", "enable <board> in the flash recipe", or
  "add <board> to qcom-deb-images". Covers bumping the pinned qcom-ptool /
  qcom-dtb-metadata when a board needs it, and cross-referencing meta-qcom for a
  board already enabled there. NOT for updating an existing board's binaries
  (use qcom-deb-update-boot-binaries) or bumping deps for their own sake (use
  qcom-deb-update-deps).
---

# Add a board to the debos flash recipe

## What this does

Every flashable board in this repo is one entry in the Go-`text/template` board
list at the top of `debos-recipes/qualcomm-linux-debian-flash.yaml`. Adding a
board means appending a correct, checksum-verified entry to that list — no new
recipe logic in the common case. This skill is the checklist for gathering the
five facts a board entry needs and validating them before you write it.

For general conventions (recipe structure, checksum discipline, commit style)
see `AGENTS.md`; this skill covers only what is board-specific.

## The board entry, field by field

A board is a `dict` appended to `$boards`. Copy the closest existing entry as a
template and change these fields:

- **`name`** — the board's short name, used for the flash-dir and artifact
  names. Match the board's conventional name (usually its DTB stem, e.g.
  `qcs6490-rb3gen2`).
- **`soc_id`** — the SoC identifier. **Must** equal a `/soc` node name in the
  pinned `qcom-dtb-metadata`'s `qcom-metadata.dts`; this is what
  `build-dtb-image.sh --soc` uses to select FIT configs. If the SoC has no
  `/soc` node, the board cannot build a FIT (see qcm2290, excluded for exactly
  this reason) — bumping `qcom-dtb-metadata` may be needed (see below).
- **`ptool_platforms`** — a list of `<platform>/<storage>` strings. Each
  `<platform>` must be a directory under the pinned `qcom-ptool`'s
  `platforms/<platform>/` containing a `partitions.conf`; `<storage>` is the
  disk type (`emmc`, `nvme`, `ufs`, `spinor`). **ptool platform names follow
  marketing/board names, not SoC names** — they can differ substantially from
  `name` (e.g. IQ-X7181/IQ-X5121 EVKs both map to ptool platform
  `iq-x7181-evk`). Never assume `platform == name`; look it up.
- **`boot_binaries_download`** — a `dict` with `description`, `url`, `name`,
  `filename`, `sha256sum`. The archive is a `.zip` unpacked with `unzip`.
- **`cdt_download`** (optional) + **`cdt_filename`** — the CDT archive and the
  path to the `.bin` **inside** it. Existing CDTs are `.zip` (unpacked with
  `unzip`); if the only published CDT is a `.tar.gz`, the recipe's CDT-unpack
  step needs a small change to handle it — call that out rather than renaming
  the URL.
- **`dtb`** — `qcom/<stem>.dtb`, the DTB filename as built by the kernel.
- **`dtb_bin_type`** (optional) — `combineddtb` (default) or `multidtb`.

## Procedure

### 1. Find the DTB name

The DTB is what the board's **kernel** builds, so the kernel repo is the source
of truth. Default to the mainline Linux kernel unless the board ships from a
different tree.

- Look under `arch/arm64/boot/dts/qcom/` for the board's `.dts`; the `dtb` field
  is `qcom/<that-stem>.dtb`.
- If the board is downstream-only, use the kernel repo it actually ships from
  and note that in the commit message.

### 2. Shortcut — is the board already enabled in meta-qcom?

If the board already has a machine in
[meta-qcom](https://github.com/qualcomm-linux/meta-qcom), it has already been
mapped to concrete boot binaries, CDT, and partition layout — read them off
instead of hunting. For a machine `conf/machine/<board>.conf`:

- `KERNEL_DEVICETREE` → the `.dtb` (and thus `dtb`, `name` stem).
- `QCOM_PARTITION_FILES_SUBDIR*` → `partitions/<platform>/<storage>`, i.e. the
  ptool `<platform>` and which storages exist.
- `QCOM_BOOT_FIRMWARE` / `QCOM_CDT_FIRMWARE` → the boot/CDT recipes under
  `recipes-bsp/firmware-boot/`. Their `.bb`/`.inc` carry the exact `SRC_URI`
  (URL) and `SRC_URI[...sha256sum]` you need for `boot_binaries_download` /
  `cdt_download`, plus the in-archive `.bin` path for `cdt_filename`.
- Two boards can share all of these (e.g. IQ-X5121 reuses IQ-X7181's partitions,
  boot firmware and CDT) — such boards differ only in `name`, `soc_id`, `dtb`.

Cross-check meta-qcom's values against the actual downloaded bytes anyway
(step 4); meta-qcom may pin a different build-id than you want.

### 3. Confirm ptool has the platform (bump the pin if needed)

The pinned `qcom-ptool` must have `platforms/<platform>/partitions.conf` for
each `<platform>` in `ptool_platforms`. Check the recipe's pinned commit:

```bash
grep -n 'qcom-ptool/archive' debos-recipes/qualcomm-linux-debian-flash.yaml
# then, at that commit <sha>:
gh api "repos/qualcomm-linux/qcom-ptool/contents/platforms?ref=<sha>" --jq '.[].name'
gh api "repos/qualcomm-linux/qcom-ptool/contents/platforms/<platform>?ref=<sha>" \
    --jq '.[].name'   # expect partitions.conf and per-storage dirs
```

If the platform is missing at the pin but exists upstream, it is **acceptable to
bump the `qcom-ptool` pin** to pick it up — but do it via `qcom-deb-update-deps`
so the backwards-incompatibility review happens. Likewise, if `soc_id` has no
`/soc` node at the pinned `qcom-dtb-metadata`, bumping that pin may add it; again
route through `qcom-deb-update-deps`.

### 4. Verify every download

For the boot-binaries archive and (if present) the CDT archive, download the
exact URL and compute its checksum yourself — this is the value that goes in the
entry and that the recipe enforces:

```bash
curl -fSL -o /tmp/boot.zip "<boot_binaries url>"
sha256sum /tmp/boot.zip
unzip -l /tmp/boot.zip | head        # sanity: expected top-level dir layout
curl -fSL -o /tmp/cdt.zip "<cdt url>"
sha256sum /tmp/cdt.zip
unzip -l /tmp/cdt.zip                 # find the .bin -> cdt_filename (in-archive path)
```

Put the computed sums into `sha256sum`. If a value came from meta-qcom, confirm
it matches what you downloaded.

### 5. Write the entry and validate

- Append the `dict` in the same style as the neighbouring entries (respect any
  `{{- if eq $build_<soc> "true" }}` guards the surrounding boards use).
- Re-read the whole `{{- $boards = append … }}` block for template balance
  (`{{- if }}`/`{{- end }}`).
- Do a lint pass on the shell/YAML you touched. A full `debos` build is the real
  test; if you can't run it, say so and list what a maintainer should build to
  confirm.

### 6. Stage and report

Stage the recipe change. Report: the board's five facts, the checksums you
computed (and whether they matched meta-qcom), whether any pin bump was needed,
and any recipe-logic change required (e.g. `.tar.gz` CDT). Propose a
`feat(debos/flash): add <board>` commit message.

## Notes

- One board per commit keeps review easy; if several boards share binaries, they
  can still be separate entries in one focused commit.
- If a board needs a field the entry schema doesn't have, prefer extending the
  schema minimally and defaulting it (as `dtb_bin_type` defaults to
  `combineddtb`) over special-casing that board in shell.
