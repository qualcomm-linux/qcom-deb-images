#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# Run by d-i's preseed/late_command, after GRUB is installed, to get the
# installed system booting on the Qualcomm laptops:
#  1) install the initramfs-tools hooks from the ISO's /qli/initramfs-tools/:
#     qcom-adsp loads qcom_q6v5_pas before the root filesystem is mounted, as
#     the ADSP reset also resets the Type-C bus (and so a USB root device);
#     qcom-laptop-firmware adds the laptop's firmware (from the firmware
#     packages pkgsel installed) to the initrd
#  2) install the APT pins from the ISO's /qli/apt/, for the backports and
#     Qualcomm Linux sources apt-setup added
#  3) regenerate the initrds, so they pick up the hooks
set -e

TARGET=${1:-/target}
HOOKS_SRC=/cdrom/qli/initramfs-tools

log() {
    echo "qcom-laptop-postinst: $*"
    logger -t qcom-laptop-postinst "$*" 2>/dev/null || true
}

# Only for the T14s, going by the DT the installer booted with.
#if ! grep -q 'thinkpad-t14s' /proc/device-tree/compatible 2>/dev/null; then
#    log "not a ThinkPad T14s, skipping"
#    exit 0
#fi

for hook in \
    hooks/qcom-adsp \
    hooks/qcom-laptop-firmware \
    scripts/init-top/qcom-adsp
do
    if [ ! -f "$HOOKS_SRC/$hook" ]; then
        log "missing $HOOKS_SRC/$hook"
        exit 1
    fi

    log "installing /etc/initramfs-tools/$hook"
    mkdir -p "$(dirname "$TARGET/etc/initramfs-tools/$hook")"
    cp "$HOOKS_SRC/$hook" "$TARGET/etc/initramfs-tools/$hook"
    chmod 0755 "$TARGET/etc/initramfs-tools/$hook"
done

# The APT pins target-apt.sh generated: d-i sets up the sources, but has no
# preseed for pins.
for pref in /cdrom/qli/apt/*.pref; do
    [ -f "$pref" ] || continue
    log "installing /etc/apt/preferences.d/${pref##*/}"
    mkdir -p "$TARGET/etc/apt/preferences.d"
    cp "$pref" "$TARGET/etc/apt/preferences.d/${pref##*/}"
    chmod 0644 "$TARGET/etc/apt/preferences.d/${pref##*/}"
done

# base-installer built the kernel's initrd before the hooks were installed.
log "regenerating the initrds"
chroot "$TARGET" update-initramfs -u -k all

