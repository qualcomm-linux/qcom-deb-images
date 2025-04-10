#!/bin/sh
# SPDX-License-Identifier: BSD-3-Clause

# Input disk image and sector size
DISK_IMG="$1"
SECTOR_SIZE="$2"

# Setup loop device with specified sector size
LOOP_DEV=$(losetup -f --show -P --sector-size "$SECTOR_SIZE" "$DISK_IMG")

# Mount the second partition
mkdir -p /mnt
mount "${LOOP_DEV}p2" /mnt

# Bind mount necessary directories
mount --bind /dev /mnt/dev
mount --bind /dev/pts /mnt/dev/pts
mount --bind /proc /mnt/proc
mount --bind /sys /mnt/sys

# Dangerous
#mount --bind /tmp /mnt/tmp

# DNS from host
touch /mnt/etc/resolv.conf
mount --bind /etc/resolv.conf /mnt/etc/resolv.conf

# Chroot into /mnt and run a shell
chroot /mnt /bin/bash

# Cleanup after exiting the chroot shell
umount /mnt/etc/resolv.conf
#umount /mnt/tmp
umount /mnt/dev/pts
umount /mnt/dev
umount /mnt/proc
umount /mnt/sys
umount /mnt

# Detach the loop device
losetup -d "$LOOP_DEV"

echo "Cleanup completed."
