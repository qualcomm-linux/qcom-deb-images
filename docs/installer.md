# Installer

`installer/` contains a [live-build](https://salsa.debian.org/live-team/live-build)
configuration for a Debian installer image for Qualcomm devices, such as the
ThinkPad T14s Gen 6.

The image installs Debian from the packages in its pool, optionally adding a
network mirror. The installer boots a kernel taken from the kernel `.deb`(s)
you provide, in place of the stock d-i kernel, so it supports the same
hardware as that kernel.

The variants match the rootfs recipes'. Each has at most one desktop task in
its pool, so a desktop can be installed offline, and ticks that desktop by
default in tasksel:

| Variant | Desktop in the pool | Live system | tasksel default |
| ------- | ------------------- | ----------- | --------------- |
| `console` | none | no | none |
| `xfce` | Xfce | no | Xfce |
| `gnome` | GNOME | no | GNOME |
| `live-xfce` | Xfce | Xfce | Xfce |
| `live-gnome` | GNOME | GNOME | GNOME |

A `console` image has a smaller pool, but a non-desktop install still works
offline. A desktop is installed from the network mirror; offline, the
installer doesn't offer one. The `live-*` variants add a live system to the
same image, to try the hardware without installing anything (see
[Live system](#live-system)). Their installer is the same as the matching
non-live variant's.

This is still a prototype.

## Requirements

Build on a Debian host with:

```bash
apt -y install live-build apt-utils xorriso \
    device-tree-compiler stubble fdisk kmod zstd xz-utils \
    debian-archive-keyring
```

live-build runs natively and has to run as root.

`stubble` provides `stubblify` and the HWID database. The build extracts the
AArch64 stubs itself, from the `systemd-boot-efi:arm64` (preferred) and
`stubble:arm64` packages it downloads.

On trixie, `stubble` is in `trixie-backports`.

## Kernel requirements

The kernel `.deb` must ship its DTBs. If the kernel is split into several
packages, pass all of them: Debian's `linux-image-$KVER` is empty, and the
kernel, DTBs and modules are in `linux-binary-$KVER`, `linux-base-$KVER` and
`linux-modules-$KVER`.

The installer initrds are repacked with xz, so the kernel needs
`CONFIG_RD_XZ=y`. They keep their `initrd.gz` names, as the kernel finds the
format from the data.

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

The wrapped kernels are embedded as they are, as in the debos images. A
`CONFIG_EFI_ZBOOT=y` kernel is a PE image. A plain gzipped `Image`, such as
Debian's `linux-image-arm64`, isn't; wrapping one is untested on hardware
and needs testing.

## Build the image

```bash
make installer KERNEL_DEB=/path/to/linux-image-custom.deb
```

This runs `installer/build-installer.sh` through `sudo`, unless make is already
running as root. `SUITE` picks the Debian suite (default `trixie`), and
`INSTALLER_OPTS` passes further options to the script:

| Option | Default | Description |
| ------ | ------- | ----------- |
| `--arch` | `arm64` | target architecture; only `arm64` is supported |
| `--suite` | `trixie` | Debian suite, e.g. `forky`; `make installer` sets it from `SUITE` |
| `--variant` | `gnome` | installer variant: `console`, `xfce`, `gnome`, `live-gnome` or `live-xfce` |
| `--kernel-name` | none | kernel name shown in the boot menu's title, e.g. `qcom-laptops` |
| `--build-id` | none | build ID shown in the boot menu's title; CI passes its run ID and attempt |

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
the `apt-cache` and `apt-get` commands. `.github/workflows/installer.yml`
downloads the kernels it builds with in the same way.

The image is written as `installer/build/$ARCH/$SUITE/$VARIANT/*.img`.
Write it to a USB stick, e.g. with `bmaptool copy --nobmap` or `dd`. Each
suite and variant has its own live-build tree there. The build
refuses to run if the tree already exists, so remove it before rebuilding:

```bash
make clean-installer
```

## How it is built

`build-installer.sh` runs `lb config` in the build tree and then copies the files in
`installer/live-build/` to their places in the generated `config/`, or, for
the installer initrds, to `initrd-overlay/`. `installer/live-build/` is
grouped by topic (`kernel/`, `defaults/`, `bootloader/`, `qcom-adsp/`
etc.), and `build-installer.sh` lists every file with its destination, including the
ones it only copies for some variants. A topic's `lib/` holds scripts
that `build-installer.sh` runs; they are never copied into the image.

During `lb binary`, once the package lists have filled the pool,
`live-build/kernel/hooks/0050-replace-di-kernel.hook.binary` runs
`live-build/kernel/lib/replace-di-kernel.sh`, which replaces the kernel in
the installer's initrds with the one from the kernel `.deb`(s), along with
its modules, the preseeds and, on arm64, firmware, and repacks them with
xz:

- `live-build/kernel/lib/trim-modules.sh` drops the modules the installer
  doesn't need, following `live-build/kernel/initrd-modules.rules`.
- `live-build/firmware/lib/extract-firmware.sh` takes the firmware the
  installer needs from the firmware packages in the pool: the ADSP and
  CDSP images, as the qcom-adsp initramfs-tools hook does, but only for
  the models in its `MODELS` list (the ThinkPad X13s and T14s Gen 6), to
  keep the initrds small. Add a model there for the installer to reset
  its ADSP early. The initrds
  have no wifi firmware or drivers, as the installer installs from its
  own pool.
- The files in the topics' `preseed/` dirs go into the initrds as
  `/preseed.cfg`.
- The files in the topics' `initrd/` dirs are copied into the initrds.

The binary hooks in `live-build/defaults/hooks/` drop the stock kernel's
module udebs and udebs for unused hardware from the pool. The pool also has
tasksel's standard task and the packages in
`live-build/defaults/package-lists/`, so the installer can install a
standard system offline. `build-installer.sh` adds the
`$DESKTOP.list.binary` of the variant's desktop (`gnome` or `xfce`), so that
desktop can be installed offline too; `console` images have none. It also
adds `defaults/preseed/tasksel-$DESKTOP.cfg` (`tasksel-console.cfg` for
`console`), which sets the desktop tasksel ticks by default.

`build-installer.sh` adds further packages to the pool, from `config/packages.binary/`:

- the kernel `.deb`(s), in place of the stock kernel, which
  `0060-prune-pool.hook.binary` drops

`live-build/kernel/package-lists/linux-base.list.binary` adds the
kernel's newer `linux-base`, since base-installer installs the kernel
before backports is set up on the target, and
`live-build/dtb-loader/package-lists/dtb-loader.list.binary` adds
`stubble` and `systemd-boot-efi`, with their dependencies. On trixie,
`build-installer.sh` enables `trixie-backports` in live-build, which `stubble` comes
from, and `live-build/kernel/archives/linux-base.pref.chroot` pins
`linux-base` to it.

`live-build/firmware/package-lists/firmware.list.binary` adds the wifi and
SoC firmware packages from non-free-firmware. live-build files them under
the pool's non-free component, which the ISO's `Release` doesn't list, so
`live-build/firmware/hooks/0040-non-free-firmware.hook.binary` moves them
back to non-free-firmware. On trixie,
`live-build/firmware/archives/firmware.pref.chroot` pins them to
`trixie-backports`: trixie's WCN7850 firmware is too old for newer kernels,
and `firmware-qcom-dsp` is only in backports.

It also generates preseed lines in `config/installer-preseed.cfg` so that
base-installer installs the custom kernel and pkgsel the firmware,
`stubble` and `systemd-boot-efi`.

The image doesn't use shim, as there is a potential issue with it: on arm64,
`live-build/bootloader/hooks/0099-grub-efi-install.hook.binary` installs
Debian's signed GRUB directly as `EFI/boot/bootaa64.efi`, `build-installer.sh` passes
`--uefi-secure-boot disable`, and `0060-prune-pool.hook.binary` drops shim
and `mokutil` from the pool.

## Boot menu

On arm64, `live-build/bootloader/hooks/0110-grub-menu.hook.binary`
builds the GRUB menu:

- `Live system (GNOME)` or `Live system (Xfce)` (the default), only on `live-*`
  images, boots the
  live system (see [Live system](#live-system)).
- `Graphical installer` (the default on the non-live images) and
  `Text installer` boot the installer kernel, `/install/vmlinuz`, which
  `0100-dtb-loader.hook.binary` wraps in place with a DTB-loading EFI stub. It
  bundles every DTB of the kernel with an entry in Stubble's HWID database
  and picks the right one at boot. They boot it with GRUB's
  `linux`/`initrd`.
- `Automated install` boots the graphical installer at critical priority
  with the preseed `automated-install-preseed.cfg` from the root of the USB
  stick (from `installer/live-build/defaults/` in this
  repository). The installer takes the default
  answer where there is one and asks the rest, unless the preseed answers
  them. As shipped, it only answers what is the same for everyone (network,
  mirror, clock), and falls back to an install without a network when there
  is none. Personal settings (locale, user, ...) and partitioning are
  commented out and described: edit the file on the stick and uncomment the
  answers you want, e.g. the disk to install to, for an unattended install.

The menu's title shows the kernel name, suite, variant and build ID,
e.g. `qcom-deb-images installer (qcom-laptops forky gnome 123456789-1)`; the
kernel name and build ID are left out unless `--kernel-name` and
`--build-id` are given, as CI does.

## Live system

A `live-*` image boots a Debian live system with the variant's desktop
(`task-gnome-desktop` or `task-xfce-desktop`) from `live/filesystem.squashfs`, built by `lb chroot`
and `lb binary` from the same suite. It runs the kernel from the kernel
`.deb`(s), with their dependencies and the firmware packages, like the
installed system, and the same initramfs hooks (see
[ThinkPad T14s Gen 6](#thinkpad-t14s-gen-6)). The Stubble
hook wraps its kernel, `/live/vmlinuz-$VERSION`, like the installer's.

The live system isn't installed to disk. live-build's own
`--debian-installer live` installer copies the live squashfs to the disk, but
these images always use live-build's `cdrom` installer, so the installer
entries install from the pool as on the non-live variants. The image has to stay FAT32,
which can't hold a squashfs of 4 GiB or more, so `build-installer.sh` fails if it grows
that big.

The live system is untested on hardware and needs testing.

## Installed system

The installed system gets the custom kernel, which boots it.

apt-setup enables `non-free-firmware` on it (`live-build/defaults/preseed/`),
and `trixie-backports` on trixie, which `build-installer.sh` adds by suite. There are no
APT pins, and no Qualcomm Linux repository, unlike the debos rootfs
(`debos-recipes/qualcomm-linux-debian-rootfs.yaml`).

An install with no network gets no Debian entries in `/etc/apt/sources.list`
at all: the installer turns the mirror off, so apt-setup writes no mirror,
security, updates or backports lines (nor the `non-free-firmware`
component, which goes on the mirror line). Add the sources by hand, or install with a network, before using
`apt` on the installed system.

On arm64, `live-build/postinst/preseed/postinst.cfg` runs
`/qli/postinst.sh` from the image at the end of the install, which sources
the parts in `/qli/postinst.d/` to install the files no package ships. Some
of them come from the rootfs overlays in `debos-recipes/overlays/`, which
`build-installer.sh` copies to `/qli/overlays/` on the image:

- the APT pins, in `/etc/apt/preferences.d/`
- the initramfs hooks from the `qcom-adsp` overlay (see below)
- the kernel hook from the `dtb-loader` overlay, `50-stubble`, which wraps
  each `/boot/vmlinuz-$VERSION` in place in Stubble, along with the DTBs
  Stubble has HWIDs for (`dpkg --verify` flags the changed kernel;
  reinstalling the kernel package wraps it again)
- the `kernel-install` variant of that hook, `95-dtb-loader.install`, in
  `/etc/kernel/install.d/`. GRUB doesn't use `kernel-install`, so it
  stays unused unless the installed system switches to systemd-boot; the
  stock GRUB entries boot the wrapped kernels

They then rebuild the initrds and wrap the kernel.
Packaging these files would make it unnecessary.

The wrapped kernels are unsigned, so booting them likely needs Secure Boot
disabled.

shim is kept off the installed system too, for the same potential issue as
on the image. grub-installer always tries to install `shim-signed` and has
no preseed to skip it, so
`live-build/bootloader/initrd/usr/lib/post-base-installer.d/99qli-no-shim`
pins `shim-signed` and `shim-helpers-arm64-signed` to -1 in the target
before GRUB is installed, in `/etc/apt/preferences.d/qli-no-shim.pref`. That
install then fails, which grub-installer ignores, and the system boots
Debian's signed GRUB directly. The pin stays, so upgrades don't install shim
either.

## ThinkPad T14s Gen 6

The ADSP runs the Type-C port manager on these devices, and resetting it
makes the USB media vanish if it has already been found. So the installer
resets the ADSP early, from
`live-build/qcom-adsp/initrd/usr/lib/debian-installer-startup.d/S03qcom-adsp`,
and the installed system does the same from its initramfs: the `qcom-adsp`
initramfs-tools hook and script load `qcom_q6v5_pas` before the root
filesystem is mounted, and the hook adds the ADSP firmware from the firmware
packages to the initrd. The installed system's initrds are built by
initramfs-tools, which debian-installer always installs, unlike the rootfs
images, which use dracut. The `71qcom-adsp-blacklist` dracut module is shipped
too, but is unused unless the system is switched to dracut.

## CI

`.github/workflows/build-installer.yml` builds the images every night and on
demand, for these kernels:

- `qcom-7.2`: `linux-image-qcom-7.2` from the Qualcomm Linux APT repository,
  on trixie
- `qcom-next`: `linux-image-qcom-next` from the Qualcomm Linux APT repository,
  on trixie
- `debian`: Debian's `linux-image-arm64` (from `trixie-backports` on trixie),
  on trixie and forky
- `qcom-laptops`: the `linux-image-<version>` deb that
  `.github/workflows/linux-qcom-laptops.yml` last published to EFS, on
  trixie and forky
- `glymur-laptops`: the `linux-image-<version>` deb that
  `.github/workflows/linux-glymur-laptops.yml` last published to EFS, on
  trixie and forky

`.github/workflows/installer.yml` downloads the kernel `.deb`(s), builds
the image and uploads it gzipped as
`installer-$KERNEL-$SUITE-$VARIANT.img.gz`, with a `.img.bmap` block map
for `bmaptool copy` and its `sha256sums`. `build-installer.yml` builds
`live-xfce` and `live-gnome` for the `qcom-7.2` and `qcom-next` kernels,
and `live-gnome` for the `debian`, `qcom-laptops` and `glymur-laptops`
kernels.

The `qcom-laptops` and `glymur-laptops` kernels aren't in any APT
repository and have no metapackage, so a system installed from those
images keeps the kernel it was installed with until another one is
installed by hand. The image is built from whatever the last kernel build
published: `linux-qcom-laptops.yml` and `linux-glymur-laptops.yml` run
earlier the same day, so if that build failed, the image has the previous
day's kernel. The job summary shows which EFS build and kernel version it
used.
