# shellcheck shell=sh disable=SC2154 # TARGET, SRC: set by postinst.sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# Sourced by postinst.sh. Installs the Stubble files: a kernel hook that
# wraps each kernel in Stubble (bundling the laptops' DTBs), so GRUB's
# stock entries boot it, and the kernel-install variant of it (unused by
# GRUB, but there for a switch to systemd-boot).

install_overlay_file dtb-loader etc/kernel/postinst.d/50-stubble 0755
install_overlay_file dtb-loader etc/kernel/install.d/95-dtb-loader.install 0755

# The kernel was installed before the Stubble hook, so wrap it now; the
# hook wraps later kernels. It wraps them in place, so grub.cfg needs no
# update.
for image in "$TARGET"/boot/vmlinuz-*; do
    [ -f "$image" ] || continue
    version=${image##*/vmlinuz-}
    log "wrapping /boot/vmlinuz-$version in Stubble"
    chroot "$TARGET" /etc/kernel/postinst.d/50-stubble "$version" \
        "/boot/vmlinuz-$version"
done
