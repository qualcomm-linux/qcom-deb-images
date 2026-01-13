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
QCOM_NEXT_GIT_REPO = "https://github.com/qualcomm-linux/kernel"
QCOM_NEXT_GIT_REF = "qcom-next"
# base config to use
BASE_CONFIG = "defconfig"
# package set to build
DEB_PKG_SET = "bindeb-pkg"


def get_latest_qcom_next_tag(repo):
    """
    Find the latest qcom-next-...-date tag from the repository.
    The date is expected to be the last component of the tag.
    """
    log_i(f"Fetching tags from {repo}...")
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--tags", "--refs", repo],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        fatal(f"Failed to fetch tags from {repo}: {e.stderr}")

    latest_tag = None
    latest_date = -1

    for line in result.stdout.splitlines():
        # output format: <hash>\trefs/tags/<tag>
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        ref = parts[1]
        if not ref.startswith("refs/tags/"):
            continue
        tag = ref[len("refs/tags/"):]

        if not tag.startswith("qcom-next-"):
            continue

        # check for date at the end
        tag_parts = tag.split("-")
        date_str = tag_parts[-1]

        if len(date_str) == 8 and date_str.isdigit():
            try:
                date_val = int(date_str)
                if date_val > latest_date:
                    latest_date = date_val
                    latest_tag = tag
                elif date_val == latest_date:
                    # tie-breaker: prefer lexicographically larger tag
                    # (usually newer version)
                    if latest_tag is None or tag > latest_tag:
                        latest_tag = tag
            except ValueError:
                pass

    if latest_tag:
        log_i(f"Found latest qcom-next tag: {latest_tag}")
        return latest_tag

    log_i("No suitable qcom-next tag found, falling back to default ref")
    return QCOM_NEXT_GIT_REF


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
        "--qcom-next",
        action="store_true",
        help="Use qcom-next repository and ref defaults",
    )
    parser.add_argument(
        "fragments",
        metavar="FRAGMENT",
        type=str,
        nargs="*",
        help="Config fragments to merge",
    )
    args = parser.parse_args()

    if args.qcom_next:
        if args.repo == GIT_REPO:
            args.repo = QCOM_NEXT_GIT_REPO
        if args.ref == GIT_REF:
            args.ref = get_latest_qcom_next_tag(args.repo)

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
    # directory to store local config fragments so they can be picked up by
    # kbuild
    local_conf_dir = Path("linux/kernel/configs")
    local_conf_dir.mkdir(parents=True, exist_ok=True)

    config_targets = [BASE_CONFIG]

    for i, fragment in enumerate(args.fragments):
        if Path(fragment).exists():
            # Create a unique name for the local fragment
            local_frag_name = f"local_{i}.config"
            dest_path = local_conf_dir / local_frag_name

            log_i(f"Copying local fragment {fragment} to {dest_path}")
            with open(fragment, "r", encoding="utf-8") as f_in:
                content = f_in.read()
            with open(dest_path, "w", encoding="utf-8") as f_out:
                f_out.write(content)

            config_targets.append(local_frag_name)
        elif (Path("linux/arch/arm64/configs") / fragment).exists():
            log_i(f"Using config fragment from repo: {fragment}")
            config_targets.append(fragment)
        else:
            fatal(
                f"Config fragment '{fragment}' not found locally or in "
                f"repository (arch/arm64/configs/)."
            )

    nproc = subprocess.check_output(["nproc"], text=True).strip()
    make_base_command = [
        "make",
        f"-j{nproc}",
        "ARCH=arm64",
        "CROSS_COMPILE=aarch64-linux-gnu-",
        "DEB_HOST_ARCH=arm64",
    ]

    config_command = make_base_command + config_targets
    subprocess.run(config_command, check=True, cwd="linux")

    log_i("Building Linux deb")
    build_command = make_base_command + [DEB_PKG_SET]
    subprocess.run(build_command, check=True, cwd="linux")


if __name__ == "__main__":
    main()
