# Installer

`installer/` holds a [live-build](https://salsa.debian.org/live-team/live-build)
configuration for a Debian installer ISO for Snapdragon laptops, such as the
ThinkPad T14s Gen 6.

The ISO is a `cdrom` installer: it installs Debian from the packages in its
pool, optionally adding a network mirror. The installer boots a kernel taken
from the kernel `.deb`(s) you provide, in place of the stock d-i kernel, so it
supports the same hardware as that kernel.

This is still a prototype.

## Requirements

Build on a Debian host with:

```bash
apt -y install live-build apt-utils xorriso python3-pefile \
    device-tree-compiler stubble fdisk
```

live-build runs natively, not in the debos container, and has to run as root.

`stubble` provides `stubblify`, the Stubble EFI stub and its HWID database.
The stub in the native package matches the build host's architecture, and the
arm64 build looks for an AArch64 stub in `/usr/lib/stubble/stubble-aa64.efi`.
On an arm64 host, copy it:

```bash
cp /usr/lib/stubble/stubble.efi /usr/lib/stubble/stubble-aa64.efi
```

On other hosts, extract it from `stubble:arm64`:

```bash
dpkg --add-architecture arm64 && apt-get update
apt-get download stubble:arm64
dpkg-deb --fsys-tarfile stubble_*_arm64.deb |
    tar -xO ./usr/lib/stubble/stubble.efi > /usr/lib/stubble/stubble-aa64.efi
```

On trixie, `stubble` is in `trixie-backports`.

## Kernel requirements

The kernel `.deb` must ship its DTBs. If the kernel is split into several
packages, pass all of them: Debian's `linux-image-$KVER` is empty, and the
kernel, DTBs and modules are in `linux-binary-$KVER`, `linux-base-$KVER` and
`linux-modules-$KVER`.

For now the build writes a FAT32 disk image (`.img`, to write to a USB
stick) instead of an ISO, which the installer mounts as vfat, so the kernel
needs no ISO 9660 support. To mount an ISO, the `cdrom` installer needs it
built into the kernel, since the kernel can't load the stock `isofs` udeb,
and arm64 `defconfig` lacks it:

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

## Build the ISO

```bash
make installer KERNEL_DEB=/path/to/linux-image-custom.deb
```

This runs `installer/scripts/build.sh` through `sudo`, unless make is already
running as root. `SUITE` picks the Debian suite (default `trixie`), and
`INSTALLER_OPTS` passes further options to the script:

| Option | Default | Description |
| ------ | ------- | ----------- |
| `--arch` | host architecture | target architecture, `arm64` or `amd64` |
| `--suite` | `trixie` | Debian suite, e.g. `forky` |
| `--installer` | `cdrom` | installer type, only `cdrom` for now |
| `--qli-repo` | off | add the Qualcomm Linux APT repository to the installed system, for ISOs with a qcom kernel |

For example:

```bash
make installer KERNEL_DEB=linux-image-qcom-next.deb SUITE=forky
```

or, for a split Debian kernel:

```bash
make installer SUITE=forky \
    KERNEL_DEB="linux-image-*.deb linux-base-*.deb linux-binary-*.deb linux-modules-*.deb"
```

The image is written to `installer/build/$ARCH/$SUITE/cdrom/`. Each suite and
installer type has its own live-build tree there, with its own cache, so
rebuilds reuse the bootstrap. To start from a clean tree, remove it:

```bash
make clean-installer
```

`INITRD_FIRMWARE=0` in the environment leaves the extra firmware out of the
installer initrds (see below), which keeps them small. `APT_PROXY` fetches
packages through a proxy such as apt-cacher-ng, e.g.
`APT_PROXY=http://localhost:3142`.

## How it is built

`build.sh` runs `scripts/configure.sh`, which runs `lb config` in the build
tree and copies `installer/overlays/common/` and then
`installer/overlays/$ARCH/` over the generated `config/`, so the arch-specific
overlay wins. The overlays follow the layout of live-build's `config/`
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
system offline.

`build.sh` adds further packages to the pool, from `config/packages.binary/`:

- the kernel `.deb`(s), in place of the stock kernel, which
  `0060-drop-stock-kernel.hook.binary` drops
- on arm64, the firmware packages `fetch-firmware.sh` fetched, and `stubble`
  with its dependencies (`scripts/lib/fetch-stubble.sh`), from
  `trixie-backports` on trixie

It also generates preseed lines in `config/installer-preseed.cfg` so that
base-installer installs the custom kernel and pkgsel the firmware and
`stubble`.

## Boot menu

On arm64, `overlays/arm64/hooks/normal/0100-qcom-laptop-boot.hook.binary`
builds the GRUB menu:

- `QLI Graphical installer` (the default) and `QLI Text installer` boot a
  Stubble EFI binary, which bundles every DTB of the kernel with an entry in
  Stubble's HWID database and picks the right one at boot.

## Installed system

The installed system gets the custom kernel, which boots it.

`scripts/lib/target-apt.sh` sets up APT on it like the debos rootfs
(`debos-recipes/qualcomm-linux-debian-rootfs.yaml`), through apt-setup:
`non-free-firmware`, `trixie-backports` on trixie, and with `--qli-repo` the
Qualcomm Linux repository, so that a qcom kernel gets updates from it. The
pins for these are in `/qli/apt/` on the ISO.

On arm64, `overlays/arm64/preseed/cdrom/qcom-laptop-postinst.cfg` runs
`/qli/qcom-laptop-postinst.sh` from the ISO at the end of the install, to
install the files no package ships:

- the APT pins, in `/etc/apt/preferences.d/`
- the initramfs hooks from `/qli/initramfs-tools/` (see below)
- the kernel hooks from `/qli/stubble/kernel/`, which build
  `/boot/stubble-$VERSION.efi` (the kernel plus the DTBs Stubble has HWIDs
  for) for each kernel, and remove it with the kernel
- `/qli/stubble/grub.d/09_qcom-stubble`, which adds a GRUB entry for each
  Stubble image ahead of the stock ones, so the newest is the default
- the laptops' boot arguments (`overlays/arm64/lib/qcom-laptop-boot-args.sh`)
  in `/etc/default/grub.d/qcom-laptop.cfg`

It then rebuilds the initrds, the Stubble images and `grub.cfg`. Packaging
these files would make it unnecessary.

The Stubble images are unsigned, so booting them likely needs Secure Boot
disabled.

## ThinkPad T14s Gen 6

The ADSP runs the Type-C port manager on Snapdragon X laptops, and resetting it
makes the USB media vanish if it has already been found. So the installer
resets the ADSP early, from
`overlays/arm64/initrd/usr/lib/debian-installer-startup.d/S03qcom-adsp`, and
the installed system does the same from its initramfs: the `qcom-adsp`
initramfs hooks load `qcom_q6v5_pas` before the root filesystem is mounted,
and `qcom-laptop-firmware` adds the ADSP firmware from the firmware packages
to the initrd.

## CI

`.github/workflows/installer.yml` builds the ISO every night and on demand,
for three kernels, each on trixie and forky:

- `qcom-7.2`: `linux-image-qcom-7.2` from the Qualcomm Linux APT repository
- `qcom-next`: `linux-image-qcom-next` from the Qualcomm Linux APT repository
- `debian`: Debian's `linux-image-arm64` (from `trixie-backports` on trixie)

`.github/workflows/installer-build.yml` downloads the kernel `.deb`(s), builds
the image and uploads it gzipped as `$SUITE-$KERNEL-installer-cdrom.img.gz`,
with its `sha256sums`. It passes `--qli-repo` for the qcom kernels.
