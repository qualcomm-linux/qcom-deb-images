#!/bin/bash
# -*- mode: shell-script; indent-tabs-mode: nil; sh-basic-offset: 4; -*-
# ex: ts=8 sw=4 sts=4 et filetype=sh

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# Harmless: dracut sets hostonly_mode and moddir before it sources this
# file, so shellcheck cannot see where they are assigned.
# TODO: drop this suppression before upstreaming.
# shellcheck disable=SC2154

# The qcom-adsp module adds qcom_q6v5_pas to the initramfs, so udev
# autoloads it there on every Qualcomm platform with a PAS remote
# processor. The driver boots the remote processors as soon as it probes
# and if their firmware is not in the initramfs the boot fails and is
# never retried, leaving the DSPs down. Blacklist the driver after the
# qcom-adsp pre-udev hook has loaded it where needed.
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
    inst_hook pre-udev 31 "$moddir/qcom-adsp-blacklist-pre-udev.sh"
}
