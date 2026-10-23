#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# Shared installer settings.
# Sourced by 0110-grub-menu.hook.binary and by build.sh; not shipped
# in the ISO itself.

# Extra kernel arguments for the installer's GRUB entries and, through the
# /etc/default/grub.d/custom-boot-args.cfg build.sh generates, the target's.
# shellcheck disable=SC2034 # used by 0110-grub-menu.hook.binary, build.sh
CUSTOM_BOOT_ARGS="clk_ignore_unused pd_ignore_unused"
