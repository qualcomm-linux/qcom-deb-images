#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# Shared installer settings.
# Sourced by 0110-grub-menu.hook.binary; not shipped in the ISO
# itself.

# Extra kernel arguments for the installer's GRUB entries.
# shellcheck disable=SC2034 # used by 0110-grub-menu.hook.binary
CUSTOM_BOOT_ARGS="clk_ignore_unused pd_ignore_unused"
