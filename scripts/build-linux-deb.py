#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

import argparse
import os
import subprocess
import sys
from pathlib import Path

# git repo/ref to use

GIT_UPSTREAM = {
    "linux": {
        "repo": "https://github.com/torvalds/linux",
        "ref": "master",
        "ref_prefix": None,
    },
    "linux-next": {
        "repo": "https://git.kernel.org/pub/scm/linux/kernel/git/next/linux-next.git",  # noqa: E501
        "ref": "master",
        "ref_prefix": "next-",
    },
    "qcom-next": {
        "repo": "https://github.com/qualcomm-linux/kernel",
        "ref": "qcom-next",
        "ref_prefix": "qcom-next-",
    },
}

# base config to use
BASE_CONFIG = "defconfig"
# package set to build
DEB_PKG_SET = "bindeb-pkg"


def get_latest_dated_tag(repo, prefix):
    """
    Find the latest prefix-...-date tag from the repository.
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

        if not tag.startswith(prefix):
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

    return latest_tag


def log_i(msg):
    print(f"I: {msg}", file=sys.stderr)


def fatal(msg):
    print(f"F: {msg}", file=sys.stderr)
    sys.exit(1)


def is_kernel_tree(path):
    return all(
        (path / f).is_file()
        for f in ("Makefile", "Kconfig", "scripts/kconfig/merge_config.sh")
    )


def have_source_tree(path):
    """
    Return True if path holds a kernel source tree and False if it is
    free to clone into (missing or empty); anything else is fatal.
    """
    if not path.exists():
        return False
    if not path.is_dir():
        fatal(f"'{path}' is not a directory")
    if is_kernel_tree(path):
        return True
    if any(path.iterdir()):
        fatal(f"'{path}' is not empty and is not a Linux kernel source tree")
    return False


def describe_tree(linux_dir):
    """Describe the kernel tree, e.g. "7.2.0-rc1 (v7.2-rc1-12-gabc)"."""
    version = subprocess.check_output(
        ["make", "-s", "kernelversion"], cwd=linux_dir, text=True
    ).strip()
    git = subprocess.run(
        ["git", "describe", "--always", "--dirty"],
        cwd=linux_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    if git.returncode == 0 and git.stdout.strip():
        return f"{version} ({git.stdout.strip()})"
    return version


def resolve_fragment(fragment, linux_dir):
    """
    Find a config fragment and return a path usable from linux_dir. Look
    at the path as given, then in this repository's kernel-configs/ and
    then in the kernel tree's arch/arm64/configs/.
    """
    if Path(fragment).exists():
        return str(Path(fragment).resolve())

    repo_dir = Path(__file__).resolve().parent.parent / "kernel-configs"
    if (repo_dir / fragment).exists():
        return str(repo_dir / fragment)

    if (linux_dir / "arch" / "arm64" / "configs" / fragment).exists():
        return f"arch/arm64/configs/{fragment}"

    fatal(
        f"Config fragment '{fragment}' not found; tried the path as given,"
        f" {repo_dir}/ and {linux_dir}/arch/arm64/configs/"
    )


def check_package_installed(pkg, native_arch):
    """
    Check if a package is installed. dpkg matches an unqualified name
    against every architecture, so ask for the architecture as well and
    make sure it is the one we want: "libssl-dev" is not satisfied by
    "libssl-dev:arm64" alone.
    """
    name, _, want_arch = pkg.partition(":")
    try:
        result = subprocess.run(
            [
                "dpkg-query",
                "--show",
                "--showformat=${db:Status-Status} ${Architecture}\n",
                name,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except subprocess.SubprocessError:
        return False

    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        status, arch = parts
        if status != "installed":
            continue
        # "all" packages satisfy any architecture
        if arch in (want_arch or native_arch, "all"):
            return True

    return False


def check_dependencies():
    packages = [
        "bc",
        "bison",
        "coreutils",  # nproc
        "crossbuild-essential-arm64",  # native on arm64, cross otherwise
        "debhelper",
        "dpkg-dev",
        "flex",
        "git",
        "kmod",
        "libdw-dev",
        "libelf-dev",
        "libssl-dev",
        "libssl-dev:arm64",
        "make",
        "python3",
        "rsync",
    ]

    log_i(f"Checking build-dependencies ({' '.join(packages)})")

    try:
        native_arch = subprocess.check_output(
            ["dpkg", "--print-architecture"], text=True
        ).strip()
    except (OSError, subprocess.SubprocessError):
        fatal("dpkg not found; build-dependencies can only be checked on"
              " a Debian-based system")

    missing = []
    for pkg in packages:
        if check_package_installed(pkg, native_arch):
            continue
        missing.append(pkg)

    if missing:
        fatal(f"Missing build-dependencies: {' '.join(missing)}")


def main():
    DEFAULT_REPO = GIT_UPSTREAM["linux"]["repo"]
    DEFAULT_REF = GIT_UPSTREAM["linux"]["ref"]

    parser = argparse.ArgumentParser(description="Build Linux Deb")
    parser.add_argument(
        "--repo",
        default=None,
        help=f"Git repository to clone (default: {DEFAULT_REPO})",
    )
    parser.add_argument(
        "--ref",
        default=None,
        help=f"Git ref (branch/tag) to checkout (default: {DEFAULT_REF})",
    )
    parser.add_argument(
        "--linux-next",
        action="store_true",
        help="Use linux-next repository and ref defaults",
    )
    parser.add_argument(
        "--qcom-next",
        action="store_true",
        help="Use qcom-next repository and ref defaults",
    )
    parser.add_argument(
        "--local-dir",
        type=str,
        default=None,
        help=("Linux kernel source tree to build, cloned into if missing"
              " or empty (default: the current directory if it is a"
              " kernel source tree, otherwise ./linux)"),
    )

    parser.add_argument(
        "fragments",
        metavar="FRAGMENT",
        type=str,
        nargs="*",
        help="Config fragments to merge",
    )

    skip = parser.add_mutually_exclusive_group()
    skip.add_argument(
        "--skip-build",
        action="store_true",
        help="Skip building; just configure the source",
    )
    skip.add_argument(
        "--skip-configure",
        action="store_true",
        help="Skip configuring; build with the existing .config",
    )

    # intermixed, so that fragments can come before and after the flags
    args = parser.parse_intermixed_args()
    if args.skip_configure and args.fragments:
        parser.error("--skip-configure cannot be used with config fragments")

    if args.local_dir:
        linux_dir = Path(args.local_dir)
    elif is_kernel_tree(Path()):
        linux_dir = Path()
    else:
        linux_dir = Path("linux")
    have_tree = have_source_tree(linux_dir)

    clone_opts = (args.repo or args.ref or args.linux_next
                  or args.qcom_next)
    if have_tree and clone_opts:
        parser.error(
            f"--repo, --ref, --linux-next and --qcom-next cannot be used"
            f" with the existing kernel source tree in '{linux_dir}'"
        )

    check_dependencies()

    if have_tree:
        log_i(f"Using existing kernel source tree in {linux_dir}")
    else:
        # default settings for next trees
        git_upstream_key = None
        if args.linux_next:
            git_upstream_key = "linux-next"
        elif args.qcom_next:
            git_upstream_key = "qcom-next"

        repo = args.repo or DEFAULT_REPO
        ref = args.ref or DEFAULT_REF
        ref_prefix = GIT_UPSTREAM["linux"]["ref_prefix"]
        if git_upstream_key is not None:
            if not args.repo:
                repo = GIT_UPSTREAM[git_upstream_key]["repo"]
            if not args.ref:
                ref = GIT_UPSTREAM[git_upstream_key]["ref"]
                ref_prefix = GIT_UPSTREAM[git_upstream_key]["ref_prefix"]

        if ref_prefix:
            found_tag = get_latest_dated_tag(repo, ref_prefix)
            if found_tag:
                log_i(f"Found latest tag: {found_tag}")
                ref = found_tag
            else:
                log_i("No suitable tag found, falling back to default ref")

        log_i(f"Cloning Linux ({repo}:{ref}) into {linux_dir}")
        subprocess.run(
            [
                "git",
                "clone",
                "--depth=1",
                "--branch",
                ref,
                repo,
                str(linux_dir),
            ],
            check=True,
        )

    log_i(f"Building {describe_tree(linux_dir)} from {linux_dir}")

    nproc = subprocess.check_output(["nproc"], text=True).strip()
    make_base_command = [
        "make",
        f"-j{nproc}",
        "ARCH=arm64",
        "CROSS_COMPILE=aarch64-linux-gnu-",
        "DEB_HOST_ARCH=arm64",
    ]

    nproc = subprocess.check_output(["nproc"], text=True).strip()
    make_base_command = [
        "make",
        f"-j{nproc}",
        "ARCH=arm64",
        "CROSS_COMPILE=aarch64-linux-gnu-",
        "DEB_HOST_ARCH=arm64",
    ]

    if args.skip_configure:
        if not (linux_dir / ".config").exists():
            fatal(f"--skip-configure needs an existing {linux_dir}/.config")
        log_i("Using existing .config")
    else:
        config_targets = [
            resolve_fragment(f, linux_dir) for f in args.fragments
        ]

        if (linux_dir / ".config").exists():
            log_i("Replacing .config (previous one kept as .config.old)")

        log_i(f"Configuring Linux (base config: {BASE_CONFIG})")
        # Create base defconfig first
        subprocess.run(make_base_command + [BASE_CONFIG], check=True,
                       cwd=linux_dir)

        # Merge config fragments using merge_config.sh for proper
        # dependency handling
        if config_targets:
            merge_command = [
                "scripts/kconfig/merge_config.sh", "-m", "-r", ".config"
            ]
            merge_command.extend(config_targets)
            subprocess.run(
                merge_command,
                check=True,
                cwd=linux_dir,
                env={**os.environ, "ARCH": "arm64"},
            )

            # Finalize config with olddefconfig
            subprocess.run(
                make_base_command + ["olddefconfig"],
                check=True,
                cwd=linux_dir
            )

    if args.skip_build:
        log_i("Kernel source configured; skipping build as requested")
        return

    log_i("Building Linux deb")
    build_command = make_base_command + [DEB_PKG_SET]
    subprocess.run(build_command, check=True, cwd=linux_dir)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        fatal("Interrupted")
    except subprocess.CalledProcessError as e:
        cmd = " ".join(str(arg) for arg in e.cmd)
        fatal(f"Command failed with exit status {e.returncode}: {cmd}")
