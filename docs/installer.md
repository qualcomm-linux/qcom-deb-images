# Installer

`installer/` contains a [live-build](https://salsa.debian.org/live-team/live-build)
configuration for a Debian installer image for Qualcomm devices, such as the
ThinkPad T14s Gen 6.

The image is a `cdrom` installer: it installs Debian from the packages in its
pool, optionally adding a network mirror. The installer boots a kernel taken
from the kernel `.deb`(s) you provide, in place of the stock d-i kernel, so it
supports the same hardware as that kernel.

The `live` installer type adds an Xfce live system to the same image, to try
the hardware without installing anything (see [Live system](#live-system)).
Its installer is the same as `cdrom`'s.

The `netinst` installer type is `cdrom` without the GNOME and Xfce desktop
tasks in its pool, so the image is smaller. Despite the name, it isn't a
netinst image like Debian's, which fetches everything beyond the base system
from the mirror: apart from the desktops, its pool is the same as `cdrom`'s,
so a non-desktop install still works offline. A desktop is installed from the
network mirror; offline, the installer doesn't offer one.

This is still a prototype.

## Requirements

Build on a Debian host with:

```bash
apt -y install live-build apt-utils xorriso python3-pefile \
    device-tree-compiler stubble fdisk kmod zstd xz-utils \
    debian-archive-keyring
```

live-build runs natively and has to run as root.

`stubble` provides `stubblify` and the HWID database. The build extracts the
AArch64 Stubble stub itself, from the `stubble:arm64` package it downloads.

On trixie, `stubble` is in `trixie-backports`.

## Kernel requirements

The kernel `.deb` must ship its DTBs. If the kernel is split into several
packages, pass all of them: Debian's `linux-image-$KVER` is empty, and the
kernel, DTBs and modules are in `linux-binary-$KVER`, `linux-base-$KVER` and
`linux-modules-$KVER`.

The build writes a FAT32 disk image (`.img`, to write to a USB stick) rather
than an ISO, which the installer mounts as vfat, so the kernel needs no
ISO 9660 support. To mount an ISO, the `cdrom` installer needs it built into
the kernel, since the kernel can't load the stock `isofs` udeb, and arm64
`defconfig` lacks it:

```
CONFIG_ISO9660_FS=m
CONFIG_JOLIET=y
CONFIG_ZISOFS=y
```

The Stubble boot entries embed the kernel image, which must be a PE image.
A `CONFIG_EFI_ZBOOT=y` kernel is one. A plain gzipped `Image`, such as
Debian's `linux-image-arm64`, isn't, so the build embeds the unpacked `Image`
instead (a PE image with `CONFIG_EFI_STUB=y`).

The Stubble entries with a gzipped (non-ZBOOT) kernel are untested on
hardware and need testing.

## Build the image

```bash
make installer KERNEL_DEB=/path/to/linux-image-custom.deb
```

This runs `installer/scripts/build.sh` through `sudo`, unless make is already
running as root. `SUITE` picks the Debian suite (default `trixie`), and
`INSTALLER_OPTS` passes further options to the script:

| Option | Default | Description |
| ------ | ------- | ----------- |
| `--arch` | `arm64` | target architecture, `arm64` or `amd64` |
| `--suite` | `trixie` | Debian suite, e.g. `forky`; `make installer` sets it from `SUITE` |
| `--installer` | `cdrom` | installer type, `cdrom`, `live` (`cdrom` plus a live system) or `netinst` (`cdrom` without the desktops in the pool) |
| `--qli-repo` | off | add the Qualcomm Linux APT repository to the installed system, for images with a qcom kernel |
| `--kernel-name` | none | kernel name shown in the boot menu's title, e.g. `qcom-laptops` |
| `--build-id` | none | build ID shown in the boot menu's title; CI passes its run ID and attempt |
| `--uefi-shell` | none | UEFI shell (`shellaa64.efi`) for the boot menu's advanced options; CI passes a pinned release |

For example, to build an arm64 installer for forky with Debian's kernel,
download the packages of the kernel that `linux-image-arm64` depends on (on
a non-arm64 host, first run `dpkg --add-architecture arm64 && apt-get
update`), then pass them all:

```bash
KVER=$(apt-cache depends linux-image-arm64:arm64 |
    sed -n 's/^  Depends: linux-image-\([^:]*\).*/\1/p')
mkdir kernel
(cd kernel && apt-get download linux-{image,base,binary,modules}-$KVER:arm64)
make installer SUITE=forky INSTALLER_OPTS="--arch arm64" KERNEL_DEB="kernel/*.deb"
```

`--arch arm64` is only needed on a non-arm64 host. For trixie, the kernel
comes from `trixie-backports`: enable it and add `-t trixie-backports` to
the `apt-cache` and `apt-get` commands. `.github/workflows/installer-build.yml`
downloads the kernels it builds with in the same way.

The image is written as `installer/build/$ARCH/$SUITE/$INSTALLER/*.img`.
Write it to a USB stick, e.g. with `bmaptool copy --nobmap` or `dd`. Each
suite and installer type has its own live-build tree there, with its own
cache, so rebuilds reuse the bootstrap. To start from a clean tree, remove
it:

```bash
make clean-installer
```

A few settings are read from the environment, or can be given to make, which
passes them on through `sudo`:

- `INITRD_FIRMWARE=0` leaves the extra firmware out of the installer initrds
  (see below), which keeps them small.
- `FIRMWARE_DIR` takes the installer initrds' firmware from an absolute path
  to an existing firmware tree instead of fetching it, for repeated or
  offline builds. The installed system then gets no firmware packages.

## How it is built

`build.sh` runs `scripts/configure.sh`, which runs `lb config` in the build
tree and copies `installer/overlays/common/`, then `installer/overlays/$ARCH/`
and then the installer type's overlay (`installer/overlays/$INSTALLER/`, after
`installer/overlays/cdrom/` for `live`) over the generated `config/`, so the
later overlays win. The overlays follow the layout of live-build's `config/`
directory.

After `lb installer`, `scripts/replace-di-kernel.sh` replaces the kernel in
the installer's initrds with the one from the kernel `.deb`(s), along with its
modules, the preseeds and, on arm64, firmware:

- `scripts/lib/trim-modules.sh` drops the modules the installer doesn't need,
  following `overlays/arm64/initrd-modules.rules`.
- `scripts/lib/fetch-firmware.sh` fetches the wifi and SoC firmware from
  Debian's non-free-firmware packages (`trixie-backports` on trixie, whose
  WCN7850 firmware is too old for newer kernels).
- The `preseed/cdrom/` files of the overlays go into the initrds as
  `/preseed.cfg`.
- `overlays/arm64/initrd/` is copied into the initrds.

The binary hooks in `overlays/common/hooks/normal/` drop the stock kernel's
module udebs and udebs for unused hardware from the pool. The pool also has
tasksel's standard task and the packages in
`overlays/common/package-lists/`, so the installer can install a standard
system offline. `overlays/cdrom/package-lists/` adds the GNOME and Xfce
desktop tasks to the `cdrom` and `live` pools, so a desktop can be installed
offline too; `netinst` images leave them out.

`build.sh` adds further packages to the pool, from `config/packages.binary/`:

- the kernel `.deb`(s), in place of the stock kernel, which
  `0060-prune-pool.hook.binary` drops
- the kernel's dependencies (`scripts/lib/fetch-kernel-deps.sh`), since
  base-installer installs the kernel before backports is set up on the
  target; on trixie, `linux-base` comes from `trixie-backports`
- on arm64, the firmware packages `fetch-firmware.sh` fetched, and `stubble`
  with its dependencies (`scripts/lib/fetch-stubble.sh`), from
  `trixie-backports` on trixie

It also generates preseed lines in `config/installer-preseed.cfg` so that
base-installer installs the custom kernel and pkgsel the firmware and
`stubble`.

## Boot menu

On arm64, `overlays/arm64/hooks/normal/0110-grub-menu.hook.binary`
builds the GRUB menu:

- `Live system (Xfce)` (the default), only on `live` images, boots the
  live system (see [Live system](#live-system)).
- `Graphical installer` (the default on `cdrom` and `netinst` images) and
  `Text installer` boot the installer kernel, `/install/vmlinuz`, which
  `0100-stubble.hook.binary` wraps in place in a Stubble EFI binary. It
  bundles every DTB of the kernel with an entry in Stubble's HWID database
  and picks the right one at boot.
- `Automated install` boots the graphical installer at critical priority
  with the preseed `automated-install-preseed.cfg` from the root of the USB
  stick (from `installer/overlays/arm64/includes.binary/` in this
  repository). The installer takes the default
  answer where there is one and asks the rest, unless the preseed answers
  them. As shipped, it only answers what is the same for everyone (network,
  mirror, clock), and falls back to an install without a network when there
  is none. Personal settings (locale, user, ...) and partitioning are
  commented out and described: edit the file on the stick and uncomment the
  answers you want, e.g. the disk to install to, for an unattended install.
- `Advanced options ...` holds debugging tools: `UEFI Shell` boots
  `/EFI/tools/shellaa64.efi`, which is only on the image when `build.sh`
  gets `--uefi-shell`. The shell is unsigned, so booting it likely needs
  Secure Boot disabled.

The menu's title shows the kernel name, suite, installer type and build ID,
e.g. `qcom-deb-images installer (qcom-laptops forky cdrom 123456789-1)`; the
kernel name and build ID are left out unless `--kernel-name` and
`--build-id` are given, as CI does.

## Live system

A `live` image boots a Debian live system with the Xfce desktop
(`task-xfce-desktop`) from `live/filesystem.squashfs`, built by `lb chroot`
and `lb binary` from the same suite. It runs the kernel from the kernel
`.deb`(s), with their dependencies and the firmware packages, like the
installed system, and the same initramfs hooks (see
[ThinkPad T14s Gen 6](#thinkpad-t14s-gen-6)). The Stubble
hook wraps its kernel, `/live/vmlinuz-$VERSION`, like the installer's.

The live system isn't installed to disk. live-build's own
`--debian-installer live` installer copies the live squashfs to the disk, but
these images always use the `cdrom` installer, so the installer entries
install from the pool as on a `cdrom` image. The image has to stay FAT32,
which can't hold a squashfs of 4 GiB or more, so `build.sh` fails if it grows
that big.

The live system is untested on hardware and needs testing.

## Installed system

The installed system gets the custom kernel, which boots it.

`scripts/lib/target-apt.sh` sets up APT on it like the debos rootfs
(`debos-recipes/qualcomm-linux-debian-rootfs.yaml`), through apt-setup:
`non-free-firmware`, `trixie-backports` on trixie, and with `--qli-repo` the
Qualcomm Linux repository, so that a qcom kernel gets updates from it. The
pins for these are in `/qli/apt/` on the image.

An install with no network gets no Debian entries in `/etc/apt/sources.list`
at all: the installer turns the mirror off, so apt-setup writes no mirror,
security, updates or backports lines (nor the `non-free-firmware`
component, which goes on the mirror line), and the pins have nothing to
apply to. Add the sources by hand, or install with a network, before using
`apt` on the installed system.

On arm64, `overlays/arm64/preseed/cdrom/postinst.cfg` runs
`/qli/postinst.sh` from the image at the end of the install, which sources
the parts in `/qli/postinst.d/` to install the files no package ships:

- the APT pins, in `/etc/apt/preferences.d/`
- the initramfs hooks from `/qli/initramfs-tools/` (see below)
- the kernel hook from `/qli/stubble/kernel/`, which wraps each
  `/boot/vmlinuz-$VERSION` in place in Stubble, along with the DTBs Stubble
  has HWIDs for (`dpkg --verify` flags the changed kernel; reinstalling the
  kernel package wraps it again)
- `/qli/stubble/grub.d/09_qcom-stubble`, which adds GRUB entries for the
  wrapped kernels, laid out like the stock ones with the newest as the
  default. They replace the stock entries, which have no DTB:
  `/etc/grub.d/10_linux` is diverted to
  `/usr/lib/qcom-stubble/grub.d/10_linux` (undo with
  `dpkg-divert --rename --remove /etc/grub.d/10_linux`). Unlike the stock
  entries, they set GRUB's root from the disk numbering and don't `search`
  by filesystem UUID; neither do the image's entries. On the T14s,
  `search` probing the firmware's block devices hangs.
- the custom boot arguments (`CUSTOM_BOOT_ARGS` in
  `overlays/arm64/lib/installer-config.sh`)
  in `/etc/default/grub.d/custom-boot-args.cfg`

They then rebuild the initrds, wrap the kernel and rebuild `grub.cfg`.
Packaging these files would make it unnecessary.

The wrapped kernels are unsigned, so booting them likely needs Secure Boot
disabled.

## ThinkPad T14s Gen 6

The ADSP runs the Type-C port manager on these devices, and resetting it
makes the USB media vanish if it has already been found. So the installer
resets the ADSP early, from
`overlays/arm64/initrd/usr/lib/debian-installer-startup.d/S03qcom-adsp`, and
the installed system does the same from its initramfs: the `qcom-adsp`
initramfs hooks load `qcom_q6v5_pas` before the root filesystem is mounted,
and `qcom-adsp-firmware` adds the ADSP firmware from the firmware packages
to the initrd.

## CI

`.github/workflows/installer.yml` builds the image every night and on demand,
for these kernels:

- `qcom-7.2`: `linux-image-qcom-7.2` from the Qualcomm Linux APT repository,
  on trixie
- `qcom-next`: `linux-image-qcom-next` from the Qualcomm Linux APT repository,
  on trixie and forky
- `debian`: Debian's `linux-image-arm64` (from `trixie-backports` on trixie),
  on trixie and forky
- `qcom-laptops`: the `linux-image-<version>` deb that
  `.github/workflows/linux-qcom-laptops.yml` last published to EFS, on forky

`.github/workflows/installer-build.yml` downloads the kernel `.deb`(s), builds
the image and uploads it gzipped as
`installer-$KERNEL-$SUITE-$INSTALLER.img.gz`, with a `.img.bmap` block map
for `bmaptool copy` and its `sha256sums`. `installer.yml` builds the `live`
installer type for each kernel and suite, and the `cdrom` and `netinst` types
only for the `debian` and `qcom-laptops` kernels, as they install the same
system.
It passes `--qli-repo` for the qcom kernels, and `--uefi-shell` with a
pinned release of
[pbatard/UEFI-Shell](https://github.com/pbatard/UEFI-Shell), checked
against its sha256.

The `qcom-laptops` kernel isn't in any APT repository and has no
metapackage, so a system installed from that image keeps the kernel it was
installed with until another one is installed by hand. The image is built
from whatever the last kernel build published: if that build failed, the
image has the previous day's kernel. The job summary shows which EFS build
and kernel version it used.
