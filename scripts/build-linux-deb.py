#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

import argparse
import subprocess
import sys
from pathlib import Path

# git repo/ref to use
GIT_REPO = "https://github.com/torvalds/linux"
GIT_REF = "master"
# base config to use
BASE_CONFIG = "defconfig"
# package set to build
DEB_PKG_SET = "bindeb-pkg"


def log_i(msg):
    print(f"I: {msg}", file=sys.stderr)


def fatal(msg):
    print(f"F: {msg}", file=sys.stderr)
    sys.exit(1)


def check_package_installed(pkg):
    """Check if a package is installed using dpkg."""
    try:
        # dpkg -l "${pkg}" 2>&1 | grep -q "^ii  ${pkg}"
        result = subprocess.run(
            ["dpkg", "-l", pkg],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        for line in result.stdout.splitlines():
            # Match exactly "ii  <pkg>" at start of line
            if line.startswith(f"ii  {pkg}"):
                return True
    except subprocess.SubprocessError:
        pass
    return False


def check_dependencies():
    packages = [
        # needed to clone repository
        "git",
        # will pull gcc-aarch64-linux-gnu; should pull a native compiler on
        # arm64 and a cross-compiler on other architectures
        "crossbuild-essential-arm64",
        # linux build-dependencies; see linux/scripts/package/mkdebian
        "make",
        "flex",
        "bison",
        "bc",
        "libdw-dev",
        "libelf-dev",
        "libssl-dev",
        "libssl-dev:arm64",
        # linux build-dependencies for debs
        "dpkg-dev",
        "debhelper",
        "kmod",
        "python3",
        "rsync",
        # for nproc
        "coreutils",
    ]

    log_i(f"Checking build-dependencies ({' '.join(packages)})")

    missing = []
    for pkg in packages:
        if check_package_installed(pkg):
            continue
        missing.append(pkg)

    if missing:
        fatal(f"Missing build-dependencies: {' '.join(missing)}")


def main():
    parser = argparse.ArgumentParser(description="Build Linux Deb")
    parser.add_argument(
        "--repo",
        default=GIT_REPO,
        help=f"Git repository to clone (default: {GIT_REPO})",
    )
    parser.add_argument(
        "--ref",
        default=GIT_REF,
        help=f"Git ref (branch/tag) to checkout (default: {GIT_REF})",
    )
    parser.add_argument(
        "fragments",
        metavar="FRAGMENT",
        type=str,
        nargs="*",
        help="Config fragments to merge",
    )
    args = parser.parse_args()

    check_dependencies()

    log_i(f"Cloning Linux ({args.repo}:{args.ref})")
    subprocess.run(
        [
            "git",
            "clone",
            "--depth=1",
            "--branch",
            args.ref,
            args.repo,
            "linux",
        ],
        check=True,
    )

    log_i(f"Configuring Linux (base config: {BASE_CONFIG})")
    local_config = Path("linux/kernel/configs/local.config")

    if local_config.exists():
        print(f"removed '{local_config}'")
        local_config.unlink()

    for fragment in args.fragments:
        log_i(f"Adding config fragment to local.config: {fragment}")

        # ensure parent dir exists (it should, inside cloned repo)
        local_config.parent.mkdir(parents=True, exist_ok=True)

        # append content of fragment to local.config
        with open(fragment, "r", encoding="utf-8") as f_in:
            content = f_in.read()

        with open(local_config, "a", encoding="utf-8") as f_out:
            f_out.write(content)

    nproc = subprocess.check_output(["nproc"], text=True).strip()
    make_base_command = [
        "make",
        f"-j{nproc}",
        "ARCH=arm64",
        "CROSS_COMPILE=aarch64-linux-gnu-",
        "DEB_HOST_ARCH=arm64",
    ]

    config_command = make_base_command + [BASE_CONFIG]
    if Path("kernel/configs/local.config").exists():
        config_command.append("local.config")
    subprocess.run(config_command, check=True, cwd="linux")

    log_i("Building Linux deb")
    build_command = make_base_command + [DEB_PKG_SET]
    subprocess.run(build_command, check=True, cwd="linux")


if __name__ == "__main__":
    main()
