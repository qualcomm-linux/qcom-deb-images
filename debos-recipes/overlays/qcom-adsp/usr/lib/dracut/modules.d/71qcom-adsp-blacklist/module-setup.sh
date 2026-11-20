#!/bin/bash
# -*- mode: shell-script; indent-tabs-mode: nil; sh-basic-offset: 4; -*-
# ex: ts=8 sw=4 sts=4 et filetype=sh

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# Harmless: dracut sets hostonly_mode and initdir before it sources this
# file, so shellcheck cannot see where they are assigned.
# TODO: drop this suppression before upstreaming.
# shellcheck disable=SC2154

# The qcom-adsp module adds qcom_q6v5_pas to the initramfs, so udev
# autoloads it there on every Qualcomm platform with a PAS remote
# processor. The driver boots the remote processors as soon as it probes
# and if their firmware is not in the initramfs the boot fails and is
# never retried, leaving the DSPs down. Install a modprobe blacklist for
# the driver into the initramfs, so that udev loads it after switch-root
# instead, where the firmware may be available. The blacklist is only
# honoured by udev's "modprobe -b", so the explicit modprobe in the
# qcom-adsp pre-udev hook still loads the driver, and it lives in the
# initramfs /etc so it does not persist after switch-root.
#
# This carries https://github.com/dracut-ng/dracut/pull/2796; drop it
# once dracut in Debian includes that change.

# called by dracut
check() {
    [ "$DRACUT_ARCH" = "aarch64" ] || return 1
    if [[ $hostonly_mode == "strict" ]]; then
        grep -q -E 'qcom,sc8280xp-adsp-pas|qcom,x1e80100-adsp-pas' \
            /sys/bus/platform/devices/*.remoteproc/modalias 2> /dev/null || return 1
    fi
    return 0
}

# called by dracut
depends() {
    echo qcom-adsp
}

# called by dracut
install() {
    mkdir -p "$initdir"/etc/modprobe.d
    echo "blacklist qcom_q6v5_pas" > "$initdir"/etc/modprobe.d/70qcom-adsp.conf
}
