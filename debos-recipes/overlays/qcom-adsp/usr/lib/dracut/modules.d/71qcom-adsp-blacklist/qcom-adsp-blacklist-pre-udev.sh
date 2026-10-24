#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# Stop udev from autoloading the ADSP module in the initramfs, as its
# firmware may not be there and a failed boot is never retried.
#
# Write to the initramfs /etc rather than /run/modprobe.d, because /run
# is moved into the real root, where the blacklist must not apply.
mkdir -p /etc/modprobe.d
echo "blacklist qcom_q6v5_pas" > /etc/modprobe.d/qcom-adsp-blacklist.conf
