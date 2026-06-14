#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
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


def add_qcom_dtbos_to_image_deb(linux_dir, make_base_command):
    """
    Workaround: add qcom/ DTB overlays (*.dtbo) to the linux-image deb.

    The kernel only installs (via dtbs_install) the DTBs listed in dtbs-list,
    which is built from dtb-y. Overlays declared solely as composite
    ingredients (foo-dtbs := base.dtb overlay.dtbo) never appear in dtb-y on
    their own, so the raw *.dtbo files are compiled but never packaged. The
    downstream FIT/multi-DTB image generation (qcom-dtb-metadata) needs those
    raw overlays, so without them boards whose only configs require overlays
    (e.g. monaco-evk / qcs8275) get no matching DTB and fail to boot.

    Until the upstream Makefile fix (which adds the overlays to dtb-y) lands,
    repack the produced linux-image deb to add every already-built qcom/*.dtbo
    under the same usr/lib/linux-image-<release>/qcom/ path dtbs_install uses.

    See the upstream fix that makes this unnecessary once merged
    (lore.kernel.org message-id, Vishwas Udupa):
        20260428123725.3457865-1-vudupa@qti.qualcomm.com
    """
    dts_qcom = linux_dir / "arch" / "arm64" / "boot" / "dts" / "qcom"
    dtbos = sorted(dts_qcom.glob("*.dtbo"))
    if not dtbos:
        log_i("No qcom/*.dtbo found to add to the linux-image deb; skipping")
        return

    # the kernel release is what names both the deb and the install subdir
    release = subprocess.check_output(
        make_base_command + ["-s", "kernelrelease"],
        cwd=linux_dir,
        text=True,
    ).strip()

    # bindeb-pkg writes the debs to the parent of the source tree
    deb_dir = linux_dir.resolve().parent
    image_debs = sorted(deb_dir.glob(f"linux-image-{release}_*.deb"))
    image_debs = [d for d in image_debs if "-dbg_" not in d.name]
    if len(image_debs) != 1:
        fatal(
            f"Expected exactly one linux-image-{release} deb in {deb_dir}, "
            f"found: {[d.name for d in image_debs]}"
        )
    image_deb = image_debs[0]

    install_subdir = f"usr/lib/linux-image-{release}/qcom"

    log_i(f"Adding {len(dtbos)} qcom/*.dtbo to {image_deb.name}")
    with tempfile.TemporaryDirectory() as tmp:
        extract = Path(tmp) / "deb"
        # -R preserves the control archive so we can rebuild an identical deb
        subprocess.run(
            ["dpkg-deb", "-R", str(image_deb), str(extract)],
            check=True,
        )

        dst = extract / install_subdir
        dst.mkdir(parents=True, exist_ok=True)
        for dtbo in dtbos:
            # mirror dtbs_install's "install -D -m 0644"
            shutil.copy2(dtbo, dst / dtbo.name)
            (dst / dtbo.name).chmod(0o644)

        # rebuild in place; dpkg-deb recomputes the control fields it owns
        subprocess.run(
            ["dpkg-deb", "-b", str(extract), str(image_deb)],
            check=True,
        )

    sha = hashlib.sha256(image_deb.read_bytes()).hexdigest()
    log_i(f"Repacked {image_deb.name} (sha256 {sha})")


def main():
    DEFAULT_REPO = GIT_UPSTREAM["linux"]["repo"]
    DEFAULT_REF = GIT_UPSTREAM["linux"]["ref"]

    parser = argparse.ArgumentParser(description="Build Linux Deb")
    parser.add_argument(
        "--repo",
        default=DEFAULT_REPO,
        help=f"Git repository to clone (default: {DEFAULT_REPO})",
    )
    parser.add_argument(
        "--ref",
        default=DEFAULT_REF,
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
        help=("Path to an existing Linux kernel source tree;"
              " if not set, the repo will be cloned into ./linux"),
    )

    parser.add_argument(
        "fragments",
        metavar="FRAGMENT",
        type=str,
        nargs="*",
        help="Config fragments to merge",
    )

    # Use parse_known_args to allow fragments before and after flags
    args, unknown = parser.parse_known_args()
    # Combine positional fragments with unknown args (fragments after flags)
    args.fragments = args.fragments + unknown

    # default settings for next trees
    git_upstream_key = None
    if args.linux_next:
        git_upstream_key = "linux-next"
    elif args.qcom_next:
        git_upstream_key = "qcom-next"

    ref_prefix = GIT_UPSTREAM["linux"]["ref_prefix"]
    if git_upstream_key is not None:
        if args.repo == DEFAULT_REPO:
            args.repo = GIT_UPSTREAM[git_upstream_key]["repo"]
        if args.ref == DEFAULT_REF:
            args.ref = GIT_UPSTREAM[git_upstream_key]["ref"]
            ref_prefix = GIT_UPSTREAM[git_upstream_key]["ref_prefix"]

    if ref_prefix:
        found_tag = get_latest_dated_tag(args.repo, ref_prefix)
        if found_tag:
            log_i(f"Found latest tag: {found_tag}")
            args.ref = found_tag
        else:
            log_i("No suitable tag found, falling back to default ref")

    check_dependencies()

    if args.local_dir:
        linux_dir = Path(args.local_dir)
        if not linux_dir.exists():
            fatal(f"Provided --local-dir '{linux_dir}' does not exist")
        log_i(f"Using existing kernel source at {linux_dir}")
    else:
        linux_dir = Path("linux")
        log_i(f"Cloning Linux ({args.repo}:{args.ref}) into {linux_dir}")
        subprocess.run(
            [
                "git",
                "clone",
                "--depth=1",
                "--branch",
                args.ref,
                args.repo,
                str(linux_dir),
            ],
            check=True,
        )

    log_i(f"Configuring Linux (base config: {BASE_CONFIG})")
    # directory to store local config fragments so they can be picked up by
    # kbuild
    local_conf_dir = linux_dir / "kernel" / "configs"
    local_conf_dir.mkdir(parents=True, exist_ok=True)

    config_targets = []

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

            config_targets.append(f"kernel/configs/{local_frag_name}")
        elif (linux_dir / "arch" / "arm64" / "configs" / fragment).exists():
            log_i(f"Using config fragment from repo: {fragment}")
            config_targets.append(f"arch/arm64/configs/{fragment}")
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

    # Create base defconfig first
    subprocess.run(make_base_command + [BASE_CONFIG], check=True,
                   cwd=linux_dir)

    # Merge config fragments using merge_config.sh for proper dependency
    # handling
    if config_targets:
        merge_command = [
            "scripts/kconfig/merge_config.sh", "-m", "-r", ".config"
        ]
        merge_command.extend(config_targets)
        subprocess.run(
            merge_command,
            check=True,
            cwd="linux",
            env={"ARCH": "arm64", **subprocess.os.environ}
        )

        # Finalize config with olddefconfig
        subprocess.run(
            make_base_command + ["olddefconfig"],
            check=True,
            cwd="linux"
        )

    log_i("Building Linux deb")
    build_command = make_base_command + [DEB_PKG_SET]
    subprocess.run(build_command, check=True, cwd=linux_dir)

    add_qcom_dtbos_to_image_deb(linux_dir, make_base_command)


if __name__ == "__main__":
    main()
