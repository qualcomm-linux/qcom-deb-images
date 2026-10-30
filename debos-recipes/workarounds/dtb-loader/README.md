# Automatically load DTBs when the boot firmware does not provide one

Tracking issue: https://github.com/qualcomm-linux/qcom-deb-images/issues/227

## Purpose

Some Qualcomm Compute platforms, such as Snapdragon Windows on Arm laptops,
do not provide a suitable device tree through their boot firmware. Without
one, Linux cannot boot correctly.

## Changes

Install kernel-install and kernel postinst hooks which wrap each installed
kernel image with a systemd-stub based DTB loader, the device trees of these
machines and their boot firmware signatures (CHID). Install the `stubble` and
`systemd-boot-efi` packages the hooks need.

## References

- https://github.com/ubuntu/stubble/pull/89

## Removal conditions

Remove this workaround once automatic device tree selection is supported by
the boot components available in Debian for every suite we build.
