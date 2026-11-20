# shellcheck shell=sh disable=SC2154 # TARGET: set by postinst.sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# Sourced by postinst.sh. Installs the rootfs' qcom-adsp initramfs-tools
# files and dracut module and rebuilds the initrds. The initramfs-tools
# files load qcom_q6v5_pas before the root filesystem is mounted, as the
# ADSP reset also resets the Type-C bus (and so a USB root device), and
# add the ADSP firmware (from the firmware packages) to the initrd. The
# dracut module, which blacklists the driver afterwards, is shipped but
# not used yet: d-i's base-installer always installs initramfs-tools,
# even on forky where the rootfs uses dracut, so it only takes effect if
# the system is switched to dracut later.

for file in \
    etc/initramfs-tools/hooks/qcom-adsp \
    etc/initramfs-tools/scripts/init-top/qcom-adsp \
    usr/lib/dracut/modules.d/71qcom-adsp-blacklist/module-setup.sh \
    usr/lib/dracut/modules.d/71qcom-adsp-blacklist/qcom-adsp-blacklist-pre-udev.sh
do
    install_overlay_file qcom-adsp "$file" 0755
done

# base-installer built the kernel's initrd before the hooks were installed.
log "regenerating the initrds"
chroot "$TARGET" update-initramfs -u -k all
