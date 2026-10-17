#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# Run by d-i's preseed/late_command, after GRUB is installed, to get the
# installed system booting on the Qualcomm laptops. The kernel, firmware,
# stubble and APT sources come natively from d-i and the pool; this only
# installs the files from the ISO's /qli/ that no package ships, then
# rebuilds what depends on them:
#  - initramfs-tools/: qcom-adsp loads qcom_q6v5_pas before the root
#    filesystem is mounted, as the ADSP reset also resets the Type-C bus
#    (and so a USB root device); qcom-laptop-firmware adds the laptop's
#    firmware (from the firmware packages) to the initrd
#  - apt/: the pins for the backports and Qualcomm Linux sources apt-setup
#    added; d-i has no preseed for pins
#  - stubble/: kernel hooks that build a Stubble image (the kernel plus
#    the laptops' DTBs) per kernel, a grub.d script whose GRUB entries for
#    them replace the stock 10_linux ones (diverted away, as they have no
#    DTB), and the laptops' boot arguments for GRUB
set -e

TARGET=${1:-/target}
SRC=/cdrom/qli

log() {
    echo "qcom-laptop-postinst: $*"
    logger -t qcom-laptop-postinst "$*" 2>/dev/null || true
}

# Copy <src> to /target/<dest> with <mode>; busybox has no install.
install_file() {
    if [ ! -f "$1" ]; then
        log "missing $1"
        exit 1
    fi

    log "installing $2"
    mkdir -p "$(dirname "$TARGET$2")"
    cp "$1" "$TARGET$2"
    chmod "$3" "$TARGET$2"
}

for hook in \
    hooks/qcom-adsp \
    hooks/qcom-laptop-firmware \
    scripts/init-top/qcom-adsp
do
    install_file "$SRC/initramfs-tools/$hook" "/etc/initramfs-tools/$hook" 0755
done

for pref in "$SRC"/apt/*.pref; do
    [ -f "$pref" ] || continue
    install_file "$pref" "/etc/apt/preferences.d/${pref##*/}" 0644
done

install_file "$SRC/stubble/kernel/postinst.d/zz-qcom-stubble" \
    /etc/kernel/postinst.d/zz-qcom-stubble 0755
install_file "$SRC/stubble/kernel/postrm.d/zz-qcom-stubble" \
    /etc/kernel/postrm.d/zz-qcom-stubble 0755
install_file "$SRC/stubble/grub.d/09_qcom-stubble" \
    /etc/grub.d/09_qcom-stubble 0755
install_file "$SRC/stubble/grub/qcom-laptop.cfg" \
    /etc/default/grub.d/qcom-laptop.cfg 0644

# MODULES=dep (see qcom-laptop-postinst.cfg) needs sysfs, and /proc for the
# root device, which a bare chroot lacks; d-i's in-target mounts them, but
# only for /target. Mount whichever are missing, and unmount them on exit.
mounted=
for fs in proc:proc sys:sysfs; do
    dir=$TARGET/${fs%%:*}
    if ! grep -q " $dir " /proc/mounts; then
        mount -t "${fs#*:}" "${fs#*:}" "$dir"
        mounted="$dir $mounted"
    fi
done
trap 'for dir in $mounted; do umount "$dir"; done' EXIT

# base-installer built the kernel's initrd before the hooks were installed.
log "regenerating the initrds"
chroot "$TARGET" update-initramfs -u -k all

# The kernel was installed before the Stubble hook, so build its image now;
# later kernels get theirs from the hook.
for image in "$TARGET"/boot/vmlinuz-*; do
    [ -f "$image" ] || continue
    version=${image##*/vmlinuz-}
    log "building /boot/stubble-$version.efi"
    chroot "$TARGET" /etc/kernel/postinst.d/zz-qcom-stubble "$version" \
        "/boot/vmlinuz-$version"
done

# 10_linux's entries have no DTB, so they can't boot these laptops;
# 09_qcom-stubble's replace them. grub-mkconfig runs whatever is in
# /etc/grub.d, so divert 10_linux out of it, which also keeps grub-common
# upgrades from bringing it back.
if [ ! -e "$TARGET/usr/lib/qcom-laptop/grub.d/10_linux" ]; then
    log "diverting /etc/grub.d/10_linux"
    mkdir -p "$TARGET/usr/lib/qcom-laptop/grub.d"
    chroot "$TARGET" dpkg-divert --local --rename \
        --divert /usr/lib/qcom-laptop/grub.d/10_linux /etc/grub.d/10_linux
fi

log "updating grub.cfg"
chroot "$TARGET" update-grub
