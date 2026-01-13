#!/bin/sh
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

set -eu

cat <<EOF

==  WARNING  ==

This script is deprecated. Please use the Python version instead:
  scripts/build-linux-deb.py

==  WARNING  ==

EOF
sleep 10

scripts_dir=$(dirname "$0")
exec "$scripts_dir/build-linux-deb.py" "$@"
