#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# Shared kernel bring-up arguments for Qualcomm (Snapdragon) laptops, e.g.
# the ThinkPad T14s Gen 6.
# Sourced by 0100-qcom-laptop-boot.hook.binary, for the installer's GRUB
# entries, and by build.sh, which generates the target's
# /etc/default/grub.d/qcom-laptop.cfg from it; not shipped in the ISO itself.

#QCOM_LAPTOP_BOOT_ARGS="clk_ignore_unused pd_ignore_unused loglevel=8 ignore_loglevel"
# shellcheck disable=SC2034 # used by 0100-qcom-laptop-boot.hook.binary, build.sh
QCOM_LAPTOP_BOOT_ARGS="clk_ignore_unused pd_ignore_unused"
