#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# TODO: temporary workaround, remove once the installed system sets up its
# DTB properly (e.g. via stubble or a /etc/grub.d snippet).
#
# Run by d-i's preseed/late_command, after GRUB is installed. Debian's GRUB
# doesn't load a devicetree, and the T14s doesn't boot without one, so:
#  1) install the initramfs-tools hooks from the ISO's /qli/initramfs-tools/:
#     qcom-adsp loads qcom_q6v5_pas before the root filesystem is mounted, as
#     the ADSP reset also resets the Type-C bus (and so a USB root device);
#     qcom-laptop-firmware adds the laptop's firmware to the initrd. Also copy
#     the ADSP firmware from the ISO's /qli/firmware/ to the target, which
#     has none otherwise
#  2) trixie only: add the trixie-backports apt source and install its
#     kernel, which is newer than the one base-installer put in from the
#     ISO's pool. Needs the network
#  3) regenerate the initrds, so they pick up the hooks
#  4) copy the T14s DTB the installer booted with, from the ISO's
#     install/dtbs/ (see 0100-qcom-laptop-boot.hook.binary), to /boot
#  5) add a devicetree line after each linux line in grub.cfg
#
# The DTB comes from the installer's kernel, not the installed one's. The
# grub.cfg edit is lost the next time update-grub runs (e.g. on a kernel
# upgrade), so it only gets the installed system booting.
set -e

TARGET=${1:-/target}
DTB=x1e78100-lenovo-thinkpad-t14s.dtb
SRC=/cdrom/install/dtbs/$DTB
GRUB_CFG="$TARGET/boot/grub/grub.cfg"
HOOKS_SRC=/cdrom/qli/initramfs-tools
FIRMWARE_SRC=/cdrom/qli/firmware
FIRMWARE_DIR=qcom/x1e80100/LENOVO/21N1

log() {
    echo "t14s-dtb: $*"
    logger -t t14s-dtb "$*" 2>/dev/null || true
}

# Only for the T14s, going by the DT the installer booted with.
#if ! grep -q 'thinkpad-t14s' /proc/device-tree/compatible 2>/dev/null; then
#    log "not a ThinkPad T14s, skipping"
#    exit 0
#fi

# Before the kernel install below, so its postinst builds the initrd with
# them.
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

# The cdrom ISO's pool has no firmware-qcom packages, so the target has no
# ADSP firmware for qcom-laptop-firmware to add. Copy it from the ISO, with
# the .jsn files pd-mapper needs to start the ADSP's services (e.g. battery).
# Not fatal: the hook only warns if the firmware is missing.
for fw in \
    qcadsp8380.mbn \
    adsp_dtbs.elf \
    adspr.jsn \
    adsps.jsn \
    adspua.jsn \
    battmgr.jsn
do
    if [ ! -f "$FIRMWARE_SRC/$FIRMWARE_DIR/$fw" ]; then
        log "missing $FIRMWARE_SRC/$FIRMWARE_DIR/$fw"
        continue
    fi

    log "installing /usr/lib/firmware/$FIRMWARE_DIR/$fw"
    mkdir -p "$TARGET/usr/lib/firmware/$FIRMWARE_DIR"
    cp "$FIRMWARE_SRC/$FIRMWARE_DIR/$fw" "$TARGET/usr/lib/firmware/$FIRMWARE_DIR/$fw"
    chmod 0644 "$TARGET/usr/lib/firmware/$FIRMWARE_DIR/$fw"
done

# Before editing grub.cfg: the kernel's postinst runs update-grub, which
# rewrites it. Only trixie: later suites' kernels are new enough, and have
# no backports yet. A failed install (e.g. no network) keeps the stock kernel.
# shellcheck disable=SC1091
SUITE=$(. "$TARGET/etc/os-release" && echo "$VERSION_CODENAME")
if [ "$SUITE" = trixie ]; then
    log "adding trixie-backports"
    cat >"$TARGET/etc/apt/sources.list.d/trixie-backports.sources" <<EOF
Types: deb
URIs: http://deb.debian.org/debian/
Suites: trixie-backports
Components: main non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF

    # The target's sources.list still has the installer's cdrom entry (d-i only
    # comments it out later, in finish-install), and the cdrom isn't reachable
    # from in-target, so apt-get update fails on it even though the other
    # sources update fine. Let the install decide whether it can go ahead.
    if ! in-target apt-get update; then
        log "apt-get update failed for some sources (e.g. the cdrom), trying anyway"
    fi

    log "installing the trixie-backports kernel"
    if ! in-target env DEBIAN_FRONTEND=noninteractive \
            apt-get -y install -t trixie-backports linux-image-arm64; then
        log "failed to install the trixie-backports kernel, keeping the stock one"
    fi
fi

# Covers the kernels whose initrd was built before the hooks were installed:
# the one base-installer put in from the ISO's pool, which stays on other
# suites, and on trixie if the backports install failed.
# Before the grub.cfg edit below, which may exit early.
log "regenerating the initrds"
chroot "$TARGET" update-initramfs -u -k all

# Copy device-tree into /boot && set grub config to boot with it
if [ ! -f "$SRC" ]; then
    log "missing $SRC"
    exit 1
fi

log "copying $SRC to /boot/$DTB"
cp "$SRC" "$TARGET/boot/$DTB"

if [ ! -f "$GRUB_CFG" ]; then
    log "missing ${GRUB_CFG#"$TARGET"}"
    exit 1
fi

if grep -q '^[[:space:]]*devicetree[[:space:]]' "$GRUB_CFG"; then
    log "grub.cfg already loads a devicetree, leaving it alone"
    exit 0
fi

# Reuse the linux line's directory, so this also works with a separate /boot
# partition (where GRUB sees the kernel as /vmlinuz-*, not /boot/vmlinuz-*).
# Uses the target's GNU sed: the installer's busybox one may not support \n
# in the replacement.
log "adding devicetree to grub.cfg"
chroot "$TARGET" sed -i -E \
    "s#^([[:space:]]*)linux[[:space:]]+([^[:space:]]*)/vmlinuz-.*#&\n\1devicetree \2/$DTB#" \
    /boot/grub/grub.cfg
