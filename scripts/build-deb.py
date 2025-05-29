#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# Build a Debian package using sbuild; input is a yaml configuration
# with base dsc URL, debdiff file to apply and target series

import argparse
import glob
import hashlib
import os
import subprocess
import tempfile
import shutil
import yaml

# Parse command-line arguments
parser = argparse.ArgumentParser(
    description="Build a Debian source package with a debdiff."
)
parser.add_argument(
    '--config',
    type=str,
    required=True,
    help='Path to the YAML configuration file'
)
parser.add_argument(
    '--output-dir',
    type=str,
    help='Optional directory to preserve resulting files'
)
args = parser.parse_args()

# Load configuration from YAML file
with open(args.config, 'r') as f:
    config = yaml.safe_load(f)

# Create a temporary directory
with tempfile.TemporaryDirectory() as temp_dir:
    # Download the original Debian source package using dget
    dsc_url = config['dsc_url']
    subprocess.run(['dget', '-d', dsc_url], cwd=temp_dir, check=True)
    print("✅ Original source package downloaded successfully.")

    # Determine the .dsc file name
    dsc_file = os.path.join(temp_dir, os.path.basename(dsc_url))

    # Verify the SHA256 checksum of the .dsc file
    with open(dsc_file, 'rb') as f:
        file_data = f.read()
        sha256sum = hashlib.sha256(file_data).hexdigest()

    expected_sha256 = config['dsc_sha256sum']
    if sha256sum != expected_sha256:
        raise ValueError(
            f"SHA256 checksum does not match!\n"
            f"Expected: {expected_sha256}\n"
            f"Actual:   {sha256sum}"
        )
    print("✅ Checksum of original source package matched.")

    # Unpack the source package
    subprocess.run(['dpkg-source', '-x', dsc_file], cwd=temp_dir, check=True)

    # Find the unpacked directory
    unpacked_dirs = [
        d for d in os.listdir(temp_dir)
        if os.path.isdir(os.path.join(temp_dir, d))
    ]
    if not unpacked_dirs:
        raise RuntimeError("No unpacked source directory found.")
    unpacked_dir = os.path.join(temp_dir, unpacked_dirs[0])

    # Apply the debdiff
    debdiff_path = config.get('debdiff_file')
    if debdiff_path:
        if not os.path.isabs(debdiff_path):
            config_dir = os.path.dirname(args.config)
            debdiff_path = os.path.abspath(os.path.join(config_dir,
                                                        debdiff_path))
        subprocess.run(
            ['patch', '-p1', '-i', debdiff_path], cwd=unpacked_dir, check=True)
        print("✅ Debdiff applied successfully.")
    else:
        print("⚠️  No debdiff provided.")

    # Determine number of CPUs for parallel build
    nproc_result = subprocess.run(
                       ['nproc'], stdout=subprocess.PIPE, text=True,
                       check=True)
    num_cpus = nproc_result.stdout.strip()

    # Build the resulting source package using sbuild
    suite = config['suite']
    subprocess.run(
        ['sbuild', '--verbose', '-d', suite, '--no-clean-source',
         '--dpkg-source-opt=--no-check', f'-j{num_cpus}'],
        cwd=unpacked_dir,
        check=True
    )
    print("✅ Source package built successfully.")

    # Copy results if output-dir is specified
    if args.output_dir:
        os.makedirs(args.output_dir, exist_ok=True)

        # Find the .changes file
        changes_files = glob.glob(os.path.join(temp_dir, '*.changes'))
        if not changes_files:
            raise RuntimeError("No .changes file found to extract artifacts.")
        changes_file = changes_files[0]

        # Run dcmd to get the list of files
        result = subprocess.run(
            ['dcmd', changes_file],
            cwd=temp_dir,
            check=True,
            stdout=subprocess.PIPE,
            text=True
        )

        # Parse the output to get file paths
        files_to_copy = [
            line.split()[-1] for line in result.stdout.strip().splitlines()]

        # Add any *.build files
        build_files = glob.glob(os.path.join(temp_dir, '*.build'))
        files_to_copy.extend(build_files)

        # Copy files
        for file_path in files_to_copy:
            full_path = os.path.join(temp_dir, file_path)
            if os.path.exists(full_path):
                shutil.copy(full_path, args.output_dir)

        print(f"📦 Results copied to: {args.output_dir}")
