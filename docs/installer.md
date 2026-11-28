# Installer

`installer/` contains a [live-build](https://salsa.debian.org/live-team/live-build)
configuration for a Debian installer image for Qualcomm devices. For now, the
only supported device is the Glymur CRD.

The image installs Debian from the packages in its pool, optionally adding a
network mirror. The installer boots the kernel from a `linux-image` `.deb`
you provide, e.g. `linux-image-qcom-next`'s from the Qualcomm Linux APT
repository, in place of the stock d-i kernel, so it supports the same
hardware as that kernel.

The pool has GNOME's task, so the desktop can be installed offline, and
tasksel ticks it by default. The image also has a live GNOME system, to try
the hardware without installing anything (see [Live system](#live-system)).

This is still a prototype.

## Requirements

The simplest way to build is in a forky container, as CI does (see
[Build the image](#build-the-image)), which only needs podman or docker on
the host.

To build natively, on a Debian forky host, run
`installer/setup-build-host.sh` as root once. It installs the build
dependencies, `stubble` and live-build from a pinned git commit, and adds the
Qualcomm Linux APT repository for the suite the image installs (`--suite`,
default `trixie`). live-build itself has to run as root.

`stubble` provides `stubblify` and the HWID database. The build takes the
AArch64 stub from the `systemd-boot-efi:arm64` package in the image's pool.

## Kernel requirements

The kernel `.deb` is a single `linux-image-$KVER` package with the kernel
in `/boot`, its modules and its DTBs in `/usr/lib/linux-image-$KVER/`, as
the Qualcomm Linux kernel packages are.

The installer initrds are repacked with xz, so the kernel needs
`CONFIG_RD_XZ=y`. They keep their `initrd.gz` names, as the kernel finds the
format from the data.

The build writes an iso-hybrid image (`.iso`), which can be written to a USB
stick as well as burnt to a disc. The installer mounts it as ISO 9660, so the
kernel needs `CONFIG_ISO9660_FS`, see `kernel-configs/installer.config`.

The wrapped kernels are embedded as they are, as in the debos images, so
the kernel should be a `CONFIG_EFI_ZBOOT=y` PE image.

## Build the image

To build in a forky container as CI does, so the host needs only podman or
docker, run `installer/build-installer-container.sh`. It runs
`setup-build-host.sh` and `download-kernel.sh` in the container, and
downloads the kernel given by `--kernel`, `linux-image-$KERNEL` (e.g.
`qcom-next` or `qcom-7.3`), from the Qualcomm Linux APT repository for the
suite:

```bash
installer/build-installer-container.sh --suite forky --kernel qcom-next
```

A locally built kernel package can be given instead, with `--kernel` only
naming the build directory:

```bash
installer/build-installer-container.sh --suite forky --kernel my-kernel ../linux-image-custom.deb
```

It picks podman, or docker if podman is missing; `--engine` overrides that.

To build natively, on a host set up as in [Requirements](#requirements):

```bash
installer/download-kernel.sh qcom-next kernel/
make installer KERNEL_DEB=kernel/linux-image-*.deb \
    INSTALLER_OPTS="--kernel-name qcom-next"
```

This runs `installer/build-installer.sh` through `sudo`, unless make is already
running as root. `SUITE` picks the Debian suite (default `trixie`), and
`INSTALLER_OPTS` passes further options to the script:

| Option | Default | Description |
| ------ | ------- | ----------- |
| `--suite` | `trixie` | Debian suite, e.g. `forky`; `make installer` sets it from `SUITE` |
| `--kernel-name` | the kernel's version | kernel name for the build directory, e.g. `qcom-next` |

The image is written as `installer/build/$SUITE-$KERNEL/*.iso`.
Write it to a USB stick, e.g. with `bmaptool copy --nobmap` or `dd`. Each
kernel and suite has its own live-build tree there. The build
refuses to run if the tree already exists, so remove it before rebuilding:

```bash
make clean-installer
```

On an arm64 host with KVM, `installer/run-installer-qemu.sh` boots an image
in QEMU, attached as a USB stick, with a virtio disk to install to
(`installer/build/qemu-disk.qcow2` by default, kept between runs).
`--no-image` boots the installed system from that disk. It needs
`qemu-system-arm`, `qemu-utils` and `qemu-efi-aarch64`:

```bash
installer/run-installer-qemu.sh installer/build/trixie-qcom-next/*.iso
```

The installer initrds keep `virtio-gpu` and `simpledrm`, besides the
Qualcomm display driver, so the installer has a display in QEMU.

## How it is built

`build-installer.sh` runs `lb config` in the build tree and then copies the files in
`installer/live-build/` to their places in the generated `config/`.
`installer/live-build/` is grouped by topic (`kernel/`, `defaults/`,
`dtb-loader/` etc.), and `build-installer.sh` lists every file with its
destination. A topic's `lib/` holds scripts that `build-installer.sh` runs;
they are never copied into the image.

During `lb binary`, once the package lists have filled the pool,
`live-build/kernel/hooks/0050-replace-di-kernel.hook.binary` runs
`live-build/kernel/lib/replace-di-kernel.sh`, which replaces the kernel in
the installer's initrds with the one from the kernel `.deb`, along with
its modules and the preseeds, and repacks them with xz:

- `live-build/kernel/lib/trim-modules.sh` drops the modules the installer
  doesn't need, following `live-build/kernel/initrd-modules.rules`. The
  initrds have no firmware, nor wifi drivers, as the installer installs
  from its own pool.
- The files in the topics' `preseed/` dirs go into the initrds as
  `/preseed.cfg`.

The binary hooks in `live-build/defaults/hooks/` drop the stock kernel's
module udebs and udebs for unused hardware from the pool. The pool also has
tasksel's standard task and the packages in
`live-build/defaults/package-lists/`, so the installer can install a
standard system offline, and GNOME too (`gnome.list.binary`).
`defaults/preseed/tasksel-gnome.cfg` makes tasksel tick GNOME by default.

`build-installer.sh` also adds the kernel `.deb` to the pool, from
`config/packages.binary/`, in place of the stock kernel, which
`0060-prune-pool.hook.binary` drops.

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

## Boot menu

The GRUB menu is live-build's own: the live system (the default) and the
graphical and text installers, with their advanced options. The kernel
arguments the Qualcomm machines need are in `live-build/kernel/kernel-cmdline`,
which `build-installer.sh` gives to live-build's boot entries
(`--bootappend-install` and `--bootappend-live`) and, through the preseed's
`debian-installer/add-kernel-opts`, to the installed system.

The installer entries boot the installer kernel, `/install/vmlinuz`, which
`0100-dtb-loader.hook.binary` wraps in place with a DTB-loading EFI stub. It
bundles every DTB of the kernel with an entry in Stubble's HWID database
and picks the right one at boot. The live system's kernel is wrapped the same
way.

## Live system

The image boots a Debian live system with GNOME (`task-gnome-desktop`) from
`live/filesystem.squashfs`, built by `lb chroot` and `lb binary` from the
same suite. It runs the kernel from the kernel `.deb`, with its
dependencies and the firmware packages, like the installed system. The
Stubble hook wraps its kernel, `/live/vmlinuz-$VERSION`, like the installer's.

The live system can be installed to disk with Calamares, from the "Install
Debian" icon on its desktop. Calamares copies the live system to the disk,
with Debian's settings (`calamares-settings-debian`), adapted by
`live-build/live/hooks/0060-calamares.hook.chroot`:

- its APT sources use the image's suite, instead of the hard-coded trixie,
  with backports on trixie only
- it then runs `postinst.sh` (see [Installed system](#installed-system)) on
  the target, as d-i does, from `live-build/live/calamares/qli-postinst.conf`

`build-installer.sh` also gives the live system, and so the target, a GRUB
snippet with the `kernel/kernel-cmdline` args, as d-i passes them on from
the boot entry. GRUB is installed from the stick's pool
(`live/package-lists/calamares.list.binary`). Unlike a d-i install, the
target doesn't get the trixie-backports pins for the kernel's `linux-base`
and the firmware.

The image's installer entries don't use the live system. live-build's own
`--debian-installer live` installer copies the live squashfs to the disk, but
these images use live-build's `cdrom` installer, so the installer
entries install from the pool.

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

- the kernel hook from the `dtb-loader` overlay, `50-stubble`, which wraps
  each `/boot/vmlinuz-$VERSION` in place in Stubble, along with the DTBs
  Stubble has HWIDs for (`dpkg --verify` flags the changed kernel;
  reinstalling the kernel package wraps it again)
- the `kernel-install` variant of that hook, `95-dtb-loader.install`, in
  `/etc/kernel/install.d/`. GRUB doesn't use `kernel-install`, so it
  stays unused unless the installed system switches to systemd-boot; the
  stock GRUB entries boot the wrapped kernels

It then wraps the kernel.
Packaging these files would make it unnecessary.

The wrapped kernels are unsigned, so booting them likely needs Secure Boot
disabled.

The installed system boots via shim, as on a stock Debian install:
grub-installer installs `shim-signed` from the pool along with GRUB.

## CI

`.github/workflows/build-installer.yml` builds the images every night and on
demand, for these kernels:

- `qcom-next`: `linux-image-qcom-next` from the Qualcomm Linux APT repository,
  on trixie and forky
- `qcom-7.3`: `linux-image-qcom-7.3` from the Qualcomm Linux APT repository,
  on trixie

`.github/workflows/installer.yml` runs the same steps as
`build-installer-container.sh` in a forky container: `setup-build-host.sh`,
`download-kernel.sh` and `build-installer.sh`. It builds the image and uploads it gzipped as
`installer-$KERNEL-$SUITE.iso.gz`, with a `.iso.bmap` block map
for `bmaptool copy` and its `sha256sums`.
