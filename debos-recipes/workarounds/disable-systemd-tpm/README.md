# Disable systemd TPM support

Tracking issue: https://github.com/qualcomm-linux/qcom-deb-images/issues/717

## Purpose

On Snapdragon Windows on Arm laptops, the firmware advertises a TPM2 in the
EFI variables but no device is exposed for it ("TPM2 Support: firmware only,
driver unavailable" in `bootctl status`), so the systemd-tpm units wait for a
long timeout on boot.

## Changes

Purge the `systemd-tpm` package, which is recommended by `systemd`, if it is
installed.

## References

- https://github.com/qualcomm-linux/qcom-deb-images/issues/717

## Removal conditions

Remove this workaround once systemd no longer waits for a firmware-only TPM2
without a device in every suite we build.
