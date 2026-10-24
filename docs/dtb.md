# Device tree partitions

This document describes how the `dtb_a`/`dtb_b` partitions are filled at flash
time, and how the `dtb-sync` overlay keeps them in sync with the kernel when it
is upgraded on the device.

## Background

On Qualcomm boards, the UEFI firmware loads the device tree it passes to the
kernel from the `dtb_a`/`dtb_b` partitions. The flash recipe
(`debos-recipes/qualcomm-linux-debian-flash.yaml`) generates these from the
device trees of the kernel in the root filesystem (`dtbs.tar.gz`), using
`scripts/gen-dtb-image.sh`. There are two kinds of images, both a FAT
filesystem:

- **combineddtb**: a 4 MiB FAT holding `/combined-dtb.dtb`, the device tree of
  a single board.
- **multidtb**: a 2 MiB FAT holding `/qclinux_fit.img`, a FIT image with the
  device trees of all boards of a SoC, built with
  [qcom-dtb-metadata](https://github.com/qualcomm-linux/qcom-dtb-metadata).
  The firmware picks the device tree of the board from the FIT.

Each image also holds a `/qcom-deb-images-dtb.conf` marker recording what was
put in it, as shell `key=value` pairs:

```
type=combineddtb
dtb=qcom/qcs6490-rb3gen2.dtb
```

or:

```
type=multidtb
soc=hamoa
```

The firmware ignores this file.

Nothing in the default image touches these partitions after flashing, so
after a kernel upgrade, the firmware keeps passing the device tree of the
flashed kernel to the new kernel. This may work, but device trees aren't
guaranteed to be compatible across kernel versions, and the new kernel misses
any device tree fixes or new hardware support.

## The `dtb-sync` overlay

The `dtb-sync` overlay is opt-in:

```
debos -t overlays:dtb-sync debos-recipes/qualcomm-linux-debian-rootfs.yaml
```

It installs:

- `/usr/sbin/qcom-dtb-update`, which regenerates the dtb partitions from the
  device trees of the newest installed kernel, in
  `/usr/lib/linux-image-<version>/`;
- `/etc/kernel/postinst.d/zz-qcom-dtb-update` and
  `/etc/kernel/postrm.d/zz-qcom-dtb-update`, hooks running
  `qcom-dtb-update` when a kernel is installed or removed;
- `/usr/libexec/qcom-deb-images/gen-dtb-image.sh`, the same script as the
  flash recipe uses, along with qcom-dtb-metadata in
  `/usr/share/qcom-dtb-metadata` and the tools these need (`mtools`,
  `dosfstools`, `u-boot-tools` and `device-tree-compiler`).

For each of `/dev/disk/by-partlabel/dtb_a` and `dtb_b`, `qcom-dtb-update`:

1. reads the marker; partitions without one, e.g. flashed with an older flash
   bundle or by other means, are left alone;
2. generates a new image of the same kind for the newest installed kernel;
3. copies the files of the new image into the partition, keeping its existing
   FAT.

The partitions only ever follow the newest installed kernel: installing an
older kernel doesn't change them, and removing the newest kernel reverts them
to the device trees of the newest remaining one.

`qcom-dtb-update` never fails, so as not to break kernel package installation
or removal; when it can't generate an image, e.g. because the newest kernel has
no device tree for the board, it prints a warning and leaves the partition
unchanged. It does nothing in a chroot or a container, notably while building
an image.

To regenerate the partitions by hand, e.g. after flashing:

```
sudo qcom-dtb-update
```

### Loading the device tree from systemd-boot

Instead of relying on the firmware, systemd-boot can load the device tree of
the kernel it boots from the ESP; `kernel-install` adds it to the boot entry
of each kernel when `/etc/kernel/devicetree` names the board device tree,
relative to `/usr/lib/linux-image-<version>/`. The image recipe writes this
file at build time when passed the `dtb` option, e.g.
`-t dtb:qcom/qcs6490-rb3gen2.dtb`, but the resulting image is then specific to
that board.

On a generic image, run once on the device:

```
sudo qcom-dtb-update --devicetree
```

This writes the board device tree recorded in the marker of the dtb
partitions to `/etc/kernel/devicetree`, and reinstalls the newest kernel so
that its boot entry loads it; later kernels pick it up as they are installed.
This only works on combineddtb boards, as the marker of multidtb partitions
only records the SoC; on these, `qcom-dtb-update --devicetree` fails and
`/etc/kernel/devicetree` has to be written by hand, e.g.:

```
echo qcom/BOARD.dtb | sudo tee /etc/kernel/devicetree
sudo kernel-install add "$(uname -r)" "/boot/vmlinuz-$(uname -r)"
```

### Caveats

- Both `dtb_a` and `dtb_b` are updated, so a kernel with a broken device tree
  can't be rolled back by switching slots; reinstall the previous kernel and
  remove the broken one instead.
- multidtb partitions only get the device trees that qcom-dtb-metadata knows
  of, and none if the newest kernel has none for the SoC.
