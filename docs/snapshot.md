# Reproducible builds with Debian snapshots

This document describes the `snapshot` build option: what it does, how to use
it to produce a repeatable build pinned to a point in time, and how it is
implemented for developers working on the feature.

It has two audiences:

- **Users** who want to reproduce (or later re-create) a build as it would have
  been on a particular date — see [Using snapshots](#using-snapshots).
- **Developers** working on the snapshot feature itself — see
  [How it works](#how-it-works) and [Design notes and caveats](#design-notes-and-caveats).

## What problem it solves

Without pinning, a build pulls whatever package versions are current in the APT
archives *at build time*. Two builds from the same recipes a week apart can
therefore differ. The `snapshot` option pins the APT sources to a dated
[snapshot.debian.org](https://snapshot.debian.org) archive (and the equivalent
dated Qualcomm Linux archive), so that a build performed today and a build
performed months from now resolve to the *same* package versions.

The snapshot date used is recorded in the image at `/etc/buildinfo` as
`SNAPSHOT=<date>`, so any image can be traced back to the archive state it was
built from.

## Using snapshots

### The `snapshot` option

Pass a snapshot timestamp to the debos recipes via the `snapshot` variable. The
value **must** be in the form `YYYYMMDDTHHMMSSZ` (UTC, e.g.
`20260115T000000Z`). Any other format aborts the build with an error — this is
deliberate. While snapshot.debian.org also supports `YYYYMMDD`, Debusine only
supports the full form.

### Building with the Makefile

The `snapshot` variable is a debos template variable, so it is passed through
`EXTRA_DEBOS_OPTS`. It must be supplied to **both** the rootfs build and the
image build, because each recipe re-pins its own APT sources:

```bash
# 1. root filesystem + DTBs, pinned to the snapshot
EXTRA_DEBOS_OPTS="-t snapshot:20260115T000000Z" make rootfs.tar

# 2. disk image, pinned to the same snapshot
EXTRA_DEBOS_OPTS="-t snapshot:20260115T000000Z" make disk-ufs.img
```

Use the **same** timestamp for both steps. The flash recipe does not install
packages and takes no `snapshot` option.

You can combine `snapshot` with any other option, e.g. a desktop variant:

```bash
EXTRA_DEBOS_OPTS="-t snapshot:20260115T000000Z -t gnomedesktop:true" make rootfs.tar
```

### Building by calling debos directly

```bash
debos -t snapshot:20260115T000000Z debos-recipes/qualcomm-linux-debian-rootfs.yaml
debos -t snapshot:20260115T000000Z debos-recipes/qualcomm-linux-debian-image.yaml
```

(The Makefile is still recommended, as it sets memory/scratchsize defaults that
these recipes need.)

### Verifying a build

After the image boots (or by inspecting the mounted root filesystem), check:

```bash
cat /etc/buildinfo
# SNAPSHOT=20260115T000000Z
# BUILD_ID=...        (if -t buildid: was passed)
# VARIANT_ID=console  (console | xfce | gnome)
```

The presence of `SNAPSHOT=` confirms the build was pinned. Note that the
*shipped* image points its APT sources back at the live mirrors (see
[What the shipped image looks like](#what-the-shipped-image-looks-like)), so
`apt update` on the device will fetch current packages, not the snapshot — the
snapshot governs only what was installed *at build time*.

### Reproducing a build later

To re-create a build as it was on a given date:

1. Check out the same commit of this repository that was used originally.
2. Re-run the same `make` commands with the same `snapshot:` timestamp (and the
   same other options).

Because the APT sources are pinned to the dated archive, the same package
versions are resolved. See [Design notes and caveats](#design-notes-and-caveats)
for the sources that are **not** covered by snapshots and can therefore still
drift.

## How it works

The feature spans the two build recipes. Line references are to the files as of
this writing and are meant as a reading guide, not exact addresses.

### Archive-side model

Before looking at the recipes, it helps to understand what happens on the *APT
archive* side — this is the same for both the Debian archive
([snapshot.debian.org](https://snapshot.debian.org)) and the Debusine-hosted
Qualcomm Linux archive.

Each time an archive is published, it produces a fresh set of indexes
(`InRelease`, `Release`, `Packages`, etc.). Rather than discarding the previous
indexes on each new publication, the archive **retains** them. When a snapshot
is requested at a particular timestamp, the infrastructure works out which set
of indexes was *live* at that moment — in practice, the most recent publication
at or before the requested time — and serves that set.

Several consequences follow from this model:

- **Any timestamp is valid.** You do not have to pick a timestamp that
  coincides with a publication; the archive resolves any timestamp back to the
  most recent publication before it. There is no "snapshot not found" for a
  well-formed timestamp.
- **The same timestamp works for both archives simultaneously.** Because both
  the Debian and Debusine archives resolve a timestamp the same way — back to
  their own most-recent-prior publication — a single snapshot timestamp pins
  both archives coherently. This is why the recipes can use one `snapshot:`
  value across all sources.
- **Indexes are served as-is, not re-signed.** The archive returns the original
  `InRelease` exactly as it was published; it is not regenerated or re-signed
  for the snapshot. This is what makes the previous point possible, but it also
  means the served `InRelease` is, by definition, *old* — its `Valid-Until`
  will typically be in the past.
- **`Check-Valid-Until` must be disabled.** Because the served `InRelease` is an
  old, un-re-signed file, its `Valid-Until` window will usually have expired.
  APT would reject such an archive by default, so snapshot sources must set
  `Check-Valid-Until: no` (see the `snapshot_*.sources` derivation below).

### Overview

Debian ships APT sources as `deb822`-style `*.sources` files under
`/etc/apt/sources.list.d/`, each with an `Enabled: yes|no` field. The snapshot
implementation works entirely by:

1. deriving a parallel set of `snapshot_*.sources` files whose mirror URLs point
   at the dated archive, and
2. flipping `Enabled:` on/off to switch the build between the *live* mirrors and
   the *snapshot* mirrors at the right moments.

A tiny helper, `apt-snapshot-toggle`, performs the flip.

### The `apt-snapshot-toggle` helper

`debos-recipes/qualcomm-linux-debian-rootfs.yaml` installs
`/usr/local/bin/apt-snapshot-toggle` into the rootfs when `snapshot` is set. Its
logic:

```
apt-snapshot-toggle enable   # snapshot_*.sources -> Enabled: yes, all others -> Enabled: no
apt-snapshot-toggle disable  # snapshot_*.sources -> Enabled: no,  all others -> Enabled: yes
```

It walks every `*.sources` file, classifies it as a `snapshot_*` file or not,
and rewrites its `Enabled:` line accordingly. Files with no `Enabled:` field
produce a warning rather than being edited.

### Root filesystem recipe (`qualcomm-linux-debian-rootfs.yaml`)

When `snapshot` is non-empty, the following happens in order:

1. **Validate the date.** The timestamp is checked against
   `^[0-9]{8}T[0-9]{6}Z$`; an invalid value aborts the build.
2. **Bootstrap from the snapshot.** The `mmdebstrap` action points its mirror at
   `https://snapshot.debian.org/archive/debian/<SNAPSHOT>/` instead of
   `http://deb.debian.org/debian`. Without this the baseline packages come from
   the live archive. A static snapshot's `Release` may become stale, so the
   bootstrap also disables this check with
   `apt-opts: ['Acquire::Check-Valid-Until "false"']`.

   Note that `mmdebstrap` writes `--aptopt` **permanently** into
   `/etc/apt/apt.conf.d/99mmdebstrap` in the target. The recipe deletes that
   file in the next step, so the shipped image does not carry a global
   `Check-Valid-Until` override; the derived `snapshot_*.sources` set the field
   per-source instead.

   Only the main Debian archive is passed. `mmdebstrap` auto-adds `-updates` and
   `-security` entries *only when no mirror argument is given at all*.
   Since we always pass a mirror, the bootstrap sources are just `<suite> main
   contrib non-free non-free-firmware`, matching the non-snapshot behaviour. The
   `-updates` and `-security` suites are picked up by the full `*.sources` set
   later.
3. **Install the toggle helper** (`chroot: false`, written into `${ROOTDIR}`).
4. **Record the date.** The value is written to `/etc/buildinfo` as
   `SNAPSHOT=<date>` (mode 644). `/etc/buildinfo` is the single source of truth
   for the date in later steps — they read it back with
   `grep '^SNAPSHOT=' /etc/buildinfo` rather than re-templating the variable.
5. **Create the normal live `*.sources`** for Debian, `debian-backports` and the
   Qualcomm Linux (`qli`) archive — exactly as a non-snapshot build would.
6. **Derive `snapshot_*.sources`.** For each existing `*.sources` (skipping any
   already-derived `snapshot_*`), a per-source table maps the live mirror URL to
   its dated-archive rewrite:

   | Source | Live URL | Snapshot rewrite |
   | --- | --- | --- |
   | `debian` | `http://deb.debian.org/debian/` and `.../debian-security/` | `https://snapshot.debian.org/archive/debian/<SNAPSHOT>/` and `.../debian-security/<SNAPSHOT>/` |
   | `debian-backports` | `http://deb.debian.org/debian` | `https://snapshot.debian.org/archive/debian/<SNAPSHOT>/` |
   | `qli` | `https://deb.debusine.qualcomm.com/qualcomm/qli` | same URL with `/<SNAPSHOT>` appended |
   | anything else | — | **skipped** with a warning |

   Before rewriting, the step asserts the expected live URL is actually present
   in the file and **fails loudly** if not — this prevents a silent
   non-reproducible build if an upstream mirror URL changes. Each derived file
   also gets `Check-Valid-Until: no` inserted, because a static snapshot's
   `Release` file goes stale over time and APT would otherwise reject it.

   Finally, `apt-snapshot-toggle enable` switches the build over to the snapshot
   sources.
7. **Warn about unpinned sources.** When removing the legacy `sources.list` and
   before `apt-get update && apt-get full-upgrade`, any non-snapshot source that
   is still `Enabled: yes` (i.e. one with no snapshot support) triggers a warning
   that its packages will be "the latest available".
8. **All package installation** then happens against the snapshot archives.
9. **Restore live mirrors** at the end: `apt-snapshot-toggle disable`. This
   re-enables the live sources and disables the `snapshot_*` ones — but the
   `snapshot_*.sources` files themselves are **kept** in the rootfs. This is
   what carries the pinning information forward into the image build via
   `rootfs.tar`.

### Image recipe (`qualcomm-linux-debian-image.yaml`)

The image recipe installs a few more packages (`systemd-boot`,
`u-boot-efi-dtb`, `cloud-guest-utils`), so it must pin those too:

1. After unpacking `rootfs.tar`, if `snapshot` is set: `apt-snapshot-toggle
   enable` + `apt-get update`. This works because the `snapshot_*.sources` files
   are still present from the rootfs build. (No re-derivation is needed here —
   the image recipe only toggles.)
2. Package installation proceeds against the snapshot.
3. **Cleanup:** `apt-snapshot-toggle disable`, then delete
   `/etc/apt/sources.list.d/snapshot_*.sources` and
   `/usr/local/bin/apt-snapshot-toggle`.

### What the shipped image looks like

After a snapshot build, the final image:

- has its APT sources pointing at the **live** mirrors (so on-device
  `apt update` / upgrades work normally);
- contains **no** `snapshot_*.sources` files and **no** `apt-snapshot-toggle`
  helper (they are removed by the image recipe);
- records the snapshot date in `/etc/buildinfo` (`SNAPSHOT=<date>`).

In other words, the snapshot pins *what gets installed during the build*, then
gets out of the way so the running system tracks live updates.

## CI coverage

The `build-snapshot.yml` workflow builds snapshot images for both `trixie` and
`forky`. The timestamp is passed to the `debos.yml` workflow as
`debos_extra_args: -t snapshot:<timestamp>`, which that workflow hands to both
the rootfs and the image recipe.

It runs weekly (Saturday) and on demand, rather than daily or on pull requests:
these are full image builds and snapshot.debian.org is slower and more
rate-limited than the regular mirrors. It is a workflow of its own rather than
a job of the daily `build.yml` so that it can keep that slower cadence, and so
that its artifacts land in their own destination — every job of a *run* uploads
to one shared place, keyed on the run, and the daily build publishes the same
suites under the same names.

It only answers "do the snapshot code paths still work?": no LAVA job boots the
images it builds, so the build going green and the QEMU tests which `debos.yml`
runs are the coverage. Note that a build which silently fell back to the live
mirrors would also go green; check `/etc/buildinfo` in the built rootfs as
described in [Verifying a build](#verifying-a-build) when in doubt. Its
artifacts are uploaded all the same, so that a failure can be investigated. Only
the default variant is built, as the snapshot code paths don't depend on the
variant.

The timestamp is not hardcoded: the `snapshot-date` job derives it from the
committer date of the commit being built (`git log -1`, rendered as
`YYYYMMDDTHHMMSSZ` in UTC) and passes it to the build job as a job output.
It prints the value, and the commit it came from, to the job log and the run
summary.

Any well-formed timestamp resolves to the publication which was live at that
moment (see [Archive-side model](#archive-side-model)), so the value only has to
be a date the archives still serve, not one which coincides with a publication.
The commit date is such a date and needs no maintenance: it moves forward on its
own as the repository is worked on, while two runs of the same commit still pin
the same publication — so a failure points at a change in our recipes rather
than at a change in the archives.

## Design notes and caveats

- **The bootstrap resolves on the build host, not in the chroot.** Unlike the
  other snapshot sources, the `mmdebstrap` mirror is fetched by the host's APT
  before the rootfs exists, so the build host needs working HTTPS access to
  snapshot.debian.org (i.e. a CA bundle).
- **Very old snapshots may need more relaxation.** The bootstrap disables
  `Check-Valid-Until` but still requires a currently-valid archive signing key.
  Reaching back far enough that the key of the day has expired would
  additionally need `Apt::Key::gpgvcommand`; this is deliberately not enabled.
- **Both recipes need the option.** rootfs and image builds each re-pin
  independently; passing `snapshot` to only one leaves the other resolving live
  packages.
- **Not every source supports snapshots.** Only Debian (main + security),
  `debian-backports`, and the Qualcomm Linux `qli` archive are rewritten. Any
  `aptlocalrepo`/`localdebs` sources are **not** pinned; packages from them are
  whatever is current, and the build prints a warning. Reproducibility is
  therefore best-effort with respect to those sources.
- **Local kernels are not pinned.** A kernel built via
  `scripts/build-linux-deb.py` or dropped into `local-debs/` is installed
  as-is; it is not controlled by the snapshot.
- **`Check-Valid-Until: no` is required** for snapshot sources because their
  `Release` files become stale. The recipe leaves a `TODO` to drop this once
  Debusine-based snapshots are available for the `qli` archive.
- **`/etc/buildinfo` is the source of truth** for the date within a build. Steps
  read it back rather than depending on the template variable being re-passed.
- **snapshot.debian.org availability.** The service prunes and rate-limits;
  very old or very fine-grained timestamps may be slow or unavailable, which can
  make an old build harder to reproduce.

## Related files

- `debos-recipes/qualcomm-linux-debian-rootfs.yaml` — toggle helper, date
  validation/recording, `snapshot_*.sources` derivation, live-mirror restore.
- `debos-recipes/qualcomm-linux-debian-image.yaml` — re-enable snapshot for the
  image's extra package installs, then clean up.
- `.github/workflows/build-snapshot.yml` — the weekly CI build.
- `README.md` — the user-facing summary of the `snapshot` recipe option.
