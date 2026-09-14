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

## Accessing snapshot packages

An image build can be repeated with exactly the same set of packages by
using the same snapshot timestamp when they are: 1) made with the
snapshot function active; 2) all packages for the build are sourced from
apt repositories; and 3) all such apt repositories support snapshots.
But not all packages available to the image build process are embedded
into the build. In this case, users may wish to install further packages
after flashing such an image that come from the same apt repository
timestamp as was used when the image was built.

To do this, boot the image after flashing, and ensure that every apt
call uses `-o Acquire::Check-Valid-Until=false --snapshot <timestamp>`
where `<timestamp>` is the value of `SNAPSHOT=` in `/etc/buildinfo`. For
example, you might run: `apt install -y --update -o
Acquire::Check-Valid-Until=false --snapshot 20260801T000000Z
other-package-a other-package-b`.

This method comes with two caveats:

1. It is necessary to use HTTPS and to point to a trusted mirror for all
apt downloads. Without this, there is a risk of a replay attack such
that an adversary could hide a security update that was available at the
time of the snapshot requested. To ensure this check that
`/etc/apt/sources.list` (if present) and the contents of
`/etc/apt/sources.list.d/` refer only to HTTPS URLs.

2. Any use of apt _without_ the `--snapshot` argument invalidates this
method. If this is done, the packages on the system may be upgraded to
include changes released after the time of the snapshot, and downgrades
are not supported neither by this method nor by published packages in
general.

To "lock" the system to a specific snapshot so as not to require
`--snapshot` every time, you can optionally set the `APT::Snapshot`
option in `/etc/apt/apt.conf.d/`, together with
`Acquire::Check-Valid-Until` as above. For example, create a file called
`/etc/apt/apt.conf.d/snapshot.conf` with the following contents:

```
// Make the snapshot timestamp below the same as the value of SNAPSHOT=
// from /etc/buildinfo
APT::Snapshot "20260801T000000Z";
Acquire::Check-Valid-Until false;
```

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
  APT would reject such an archive by default, so building with a snapshot
  requires `Acquire::Check-Valid-Until "false"` (see below).

### Overview

APT has a native mechanism for pinning to a dated archive: setting
`APT::Snapshot "<TIMESTAMP>";` in `/etc/apt/apt.conf.d/` makes APT resolve
every configured source's packages from that dated archive automatically,
for any archive that supports snapshot metadata (Debian's and the Debusine-
hosted Qualcomm Linux `qli` archive both do). Combined with
`Acquire::Check-Valid-Until "false";` (required — see
[above](#archive-side-model)), this is a two-line config snippet that governs
resolution for the whole build.

The implementation works entirely by:

1. writing that snippet to a single file,
   `/etc/apt/apt.conf.d/99snapshot`, once `/etc/buildinfo` records the
   snapshot timestamp, and
2. deleting that one file once package installation is done.

The normal, live-mirror `*.sources` files (Debian, Debian backports, `qli`)
are never touched — no parallel `snapshot_*.sources` files, no rewriting of
mirror URLs, and no toggle helper.

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

   This step runs on the build host, *before* the target filesystem (and
   therefore `/etc/apt/apt.conf.d/`) exists, so it cannot use the
   `APT::Snapshot` config file described below; it stays a direct
   mirror-URL override, independently of the rest of the mechanism.

   Note that `mmdebstrap` writes `--aptopt` **permanently** into
   `/etc/apt/apt.conf.d/99mmdebstrap` in the target. The recipe deletes that
   file in the next step, so the shipped image does not carry a global
   `Check-Valid-Until` override; the `apt.conf.d/99snapshot` config written
   later in the build (and removed before the rootfs is packed) covers it for
   the remainder of the build instead.

   Only the main Debian archive is passed. `mmdebstrap` auto-adds `-updates` and
   `-security` entries *only when no mirror argument is given at all*.
   Since we always pass a mirror, the bootstrap sources are just `<suite> main
   contrib non-free non-free-firmware`, matching the non-snapshot behaviour. The
   `-updates` and `-security` suites are picked up by the full `*.sources` set
   later.
3. **Record the date.** The value is written to `/etc/buildinfo` as
   `SNAPSHOT=<date>` (mode 644). `/etc/buildinfo` is the single source of truth
   for the date in later steps — they read it back with
   `grep '^SNAPSHOT=' /etc/buildinfo` rather than re-templating the variable.
4. **Create the normal live `*.sources`** for Debian, `debian-backports` and the
   Qualcomm Linux (`qli`) archive — exactly as a non-snapshot build would.
   These are never rewritten or duplicated.
5. **Pin APT to the snapshot archive.** A single file,
   `/etc/apt/apt.conf.d/99snapshot`, is written:

   ```
   APT::Snapshot "<SNAPSHOT>";
   Acquire::Check-Valid-Until "false";
   ```

   From this point on, every subsequent `apt-get`/`apt` invocation in the
   chroot resolves packages through the dated archive, for any configured
   source whose archive supports snapshot metadata — currently Debian
   (main + security), `debian-backports` and `qli`.
6. **Warn about unpinned sources.** If `aptlocalrepo` is in use, a warning is
   printed that its packages have no snapshot equivalent (a local
   bind-mounted directory has no dated archive to resolve against) and will
   be whatever is currently present in that local repository.
7. **All package installation** then happens against the snapshot archives.
8. **Remove the pin** at the end: `rm -f /etc/apt/apt.conf.d/99snapshot`. The
   live `*.sources` files were never modified, so there is nothing to
   "restore" — deleting the pin file is enough to make subsequent APT
   invocations resolve against the live mirrors again.

### Image recipe (`qualcomm-linux-debian-image.yaml`)

The image recipe installs a few more packages (`systemd-boot`,
`u-boot-efi-dtb`, `cloud-guest-utils`), so it must pin those too:

1. After unpacking `rootfs.tar`, if `snapshot` is set: read `SNAPSHOT` back
   from `/etc/buildinfo` (carried forward from the rootfs build via
   `rootfs.tar`) and recreate `/etc/apt/apt.conf.d/99snapshot` with the same
   two lines as the rootfs recipe, then `apt-get update`. It is *recreated*
   rather than re-enabled, because the rootfs recipe deleted it outright
   rather than leaving it disabled.
2. Package installation proceeds against the snapshot.
3. **Cleanup:** `rm -f /etc/apt/apt.conf.d/99snapshot`.

### What the shipped image looks like

After a snapshot build, the final image:

- has its APT sources pointing at the **live** mirrors (so on-device
  `apt update` / upgrades work normally) — they were never pointed anywhere
  else;
- contains **no** `apt.conf.d/99snapshot` file (removed by the image recipe);
- records the snapshot date in `/etc/buildinfo` (`SNAPSHOT=<date>`).

In other words, the snapshot pins *what gets installed during the build*, then
gets out of the way so the running system tracks live updates.

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
- **Not every source supports snapshots.** Debian (main + security),
  `debian-backports`, and the Qualcomm Linux `qli` archive all support
  snapshot metadata, and `APT::Snapshot` pins all of them automatically. Any
  `aptlocalrepo`/`localdebs` sources are **not** pinned — a local
  bind-mounted directory has no dated-archive equivalent — packages from them
  are whatever is current, and the build prints a warning when `aptlocalrepo`
  is in use. Reproducibility is therefore best-effort with respect to those
  sources.
- **Local kernels are not pinned.** A kernel built via
  `scripts/build-linux-deb.py` or dropped into `local-debs/` is installed
  as-is; it is not controlled by the snapshot.
- **`Acquire::Check-Valid-Until "false"` is a permanent trade-off, not a
  TODO.** A snapshot's `Release` file is served exactly as it was originally
  published and is never re-signed, so its `Valid-Until` window is, by
  definition, in the past by the time it's fetched from a snapshot archive.
  Re-signing on the fly for an arbitrary requested timestamp would need
  dedicated archive infrastructure per snapshot, which is not planned;
  disabling the check and relying on HTTPS to protect the archive's
  authenticity in transit is the accepted, permanent mitigation (confirmed
  with the Debusine maintainers — see
  [issue #602](https://github.com/qualcomm-linux/qcom-deb-images/issues/602)).
- **`/etc/buildinfo` is the source of truth** for the date within a build. Steps
  read it back rather than depending on the template variable being re-passed.
- **snapshot.debian.org availability.** The service prunes and rate-limits;
  very old or very fine-grained timestamps may be slow or unavailable, which can
  make an old build harder to reproduce.

## Related files

- `debos-recipes/qualcomm-linux-debian-rootfs.yaml` — date validation and
  recording, writing/removing the `apt.conf.d/99snapshot` pin.
- `debos-recipes/qualcomm-linux-debian-image.yaml` — recreate the
  `apt.conf.d/99snapshot` pin for the image's extra package installs, then
  clean up.
- `README.md` — the user-facing summary of the `snapshot` recipe option.
