#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

"""
Download a Debian linux-image package and extract its kernel configuration.

This script sets up an isolated APT environment, downloads a specified
linux-image package, and extracts the kernel configuration file from it.
"""

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ARCH = "arm64"


def run(cmd: list, **kwargs):
    """Run command with verbose logging."""
    print("+", " ".join(cmd), file=sys.stderr)
    return subprocess.run(cmd, check=True, **kwargs)


def run_apt_cmd(root: Path, cmd: list, **kwargs):
    """Run APT command with isolated configuration."""
    apt_conf = str(root / "etc" / "apt" / "apt.conf")
    full_cmd = [cmd[0], "-c", apt_conf] + cmd[1:]
    return run(full_cmd, **kwargs)


def ensure_dirs(*paths):
    """Create directories if they don't exist."""
    for path in paths:
        Path(path).mkdir(parents=True, exist_ok=True)


def write_file(path: Path, content: str):
    """Write content to a file, creating parent directories if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def setup_isolated_apt(
    root: Path, sources_content: str, arch: str, default_release: str = None
):
    """Set up an isolated APT environment with configuration."""
    etc_apt = root / "etc" / "apt"
    etc_apt_preferences_d = etc_apt / "preferences.d"
    etc_apt_sources_list_d = etc_apt / "sources.list.d"
    var_lib_apt_lists = root / "var" / "lib" / "apt" / "lists"

    ensure_dirs(
        etc_apt_preferences_d,
        etc_apt_sources_list_d,
        var_lib_apt_lists / "partial",
    )

    write_file(etc_apt_sources_list_d / "sources.sources", sources_content)

    # Create apt.conf with all APT options
    apt_conf_lines = [
        f'Dir "{root}";',
        f'APT::Architecture "{arch}";',
        'Acquire::Languages "none";',
        'Debug::NoLocking "false";',
    ]

    if default_release:
        apt_conf_lines.append(f'APT::Default-Release "{default_release}";')

    apt_conf_content = "\n".join(apt_conf_lines) + "\n"
    write_file(etc_apt / "apt.conf", apt_conf_content)


def apt_update(root: Path):
    """Update APT package lists in the isolated environment."""
    run_apt_cmd(root, ["apt-get", "update"])


def resolve_versioned_kernel_pkg(root: Path, package_name: str) -> str:
    """Resolve a kernel package name to its specific versioned package."""
    res = run_apt_cmd(
        root,
        ["apt-cache", "depends", package_name],
        capture_output=True,
        text=True,
    )

    depends_pattern = re.compile(r"^\s*Depends:\s+(linux-image-\S+)")
    candidates = [
        match.group(1)
        for line in res.stdout.splitlines()
        if (match := depends_pattern.match(line))
    ]

    # If no candidates found, assume the provided package is already specific
    if not candidates:
        return package_name

    # Prefer non-unsigned packages
    for pkg in candidates:
        if "-unsigned-" not in pkg:
            return pkg

    # Fall back to first candidate if all are unsigned
    return candidates[0]


def apt_download_pkg(root: Path, pkg: str, download_dir: Path) -> Path:
    """Download a package using APT and return the path to the .deb file."""
    ensure_dirs(download_dir)
    run_apt_cmd(root, ["apt-get", "download", pkg], cwd=str(download_dir))

    debs = sorted(download_dir.glob(f"{pkg}_*.deb"))
    if not debs:
        debs = sorted(download_dir.glob(f"{pkg}*.deb"))
    if not debs:
        raise SystemExit(
            f"Download succeeded but deb not found for {pkg} in {download_dir}"
        )
    return debs[-1]


def extract_kernel_config_from_deb(deb_path: Path, output: Path):
    """Extract kernel configuration from a .deb file."""
    # List content to find the exact config path using subprocess pipeline
    print(f"+ dpkg-deb --fsys-tarfile {deb_path} | tar -t", file=sys.stderr)
    with subprocess.Popen(
        ["dpkg-deb", "--fsys-tarfile", str(deb_path)], stdout=subprocess.PIPE
    ) as dpkg_proc:
        tar_result = subprocess.run(
            ["tar", "-t"],
            stdin=dpkg_proc.stdout,
            capture_output=True,
            text=True,
            check=True,
        )

    paths = [line.strip() for line in tar_result.stdout.splitlines()]
    # Look for './boot/config-*' (tar usually prefixes './')
    cfg_candidates = [
        path for path in paths if path.startswith("./boot/config-")
    ]
    if not cfg_candidates:
        raise SystemExit(f"No ./boot/config-* found in {deb_path}")
    cfg_path = cfg_candidates[0]
    print(f"# Found config path in deb: {cfg_path}", file=sys.stderr)

    # Stream-extract the config using subprocess pipeline
    print(
        f"+ dpkg-deb --fsys-tarfile {deb_path} | tar -xO {cfg_path}",
        file=sys.stderr,
    )
    with subprocess.Popen(
        ["dpkg-deb", "--fsys-tarfile", str(deb_path)], stdout=subprocess.PIPE
    ) as dpkg_proc:
        tar_result = subprocess.run(
            ["tar", "-xO", cfg_path],
            stdin=dpkg_proc.stdout,
            capture_output=True,
            check=True,
        )

    # Write the extracted config to output file
    output.write_bytes(tar_result.stdout)


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Download a linux-image package and extract its config"
    )
    parser.add_argument(
        "--sources", required=True, help="path to deb822 style sources file"
    )
    parser.add_argument(
        "--output",
        default=None,
        help="output file (default: <resolved-pkg>.config)",
    )
    parser.add_argument(
        "--package",
        default=f"linux-image-{ARCH}",
        help=f"package or metapackage name (default: linux-image-{ARCH})",
    )
    parser.add_argument(
        "--default-release",
        default=None,
        help="set APT::Default-Release (e.g., 'trixie', 'testing')",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="keep working temporary directory and show config",
    )
    args = parser.parse_args()

    sources_path = Path(args.sources)
    if not sources_path.exists():
        sys.exit(f"Error: sources file '{args.sources}' not found")

    try:
        sources_content = sources_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        sys.exit(f"Error reading sources file '{args.sources}': {e}")

    with tempfile.TemporaryDirectory(
        prefix="get-debian-linux-config-", delete=not (args.debug)
    ) as temp_dir:
        work_root = Path(temp_dir)
        apt_root = work_root / "aptroot"
        dl_dir = work_root / "downloads"

        print(
            f"==> Setting up isolated APT environment in {work_root}...",
            file=sys.stderr,
        )
        setup_isolated_apt(
            apt_root, sources_content, ARCH, args.default_release
        )

        if args.debug:
            print("\n==> APT configuration dump:", file=sys.stderr)
            run_apt_cmd(apt_root, ["apt-config", "dump"])

        print("\n==> Updating package lists...", file=sys.stderr)
        apt_update(apt_root)

        print("\n==> Resolving kernel package...", file=sys.stderr)
        image_pkg = resolve_versioned_kernel_pkg(apt_root, args.package)
        print(f"==> Resolved kernel package: {image_pkg}", file=sys.stderr)

        print(f"\n==> Downloading package: {image_pkg}", file=sys.stderr)
        deb_path = apt_download_pkg(apt_root, image_pkg, dl_dir)

        print(f"\n==> Extracting config from {deb_path.name}", file=sys.stderr)
        output_path = (
            Path(args.output)
            if args.output
            else Path.cwd() / f"{image_pkg}.config"
        )
        extract_kernel_config_from_deb(deb_path, output_path)

        print(f"\nWrote: {output_path}")


if __name__ == "__main__":
    main()
