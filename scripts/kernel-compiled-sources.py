#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# List the kernel source files compiled into a kernel build, from the DWARF
# debug info of its vmlinux and modules, as shipped by its linux-image-*-dbg
# package (the debug symbols; the -debug variants are kernels of their own,
# with -dbg packages of their own); one path per line, relative to the root of
# the kernel source tree, sorted, for scripts/kernel-cves.py.
#
# Either from debug files at hand, printing the list, or, with --sbom, for the
# kernels of an image: this downloads the -dbg package of each from the APT
# source the image installed the kernel from, which
# scripts/kernel-apt-sources.py records at the image build (--apt-sources),
# with python3-apt, which checks the signature of the repository and the
# checksum of the package, and writes the list to --output as
# <source>_<version>.compiled-sources.txt. A kernel without -dbg package
# there, e.g. one removed from the repository by now, or without recorded
# APT source, is only reported with a warning on stdout, prefixed with
# "warning:"; scripts/kernel-cves.py then assesses its CVEs by version
# alone. Debian's own kernel is left out, like there.
#
# This reads the file name tables of the DWARF line programs, rather than
# only the compile units, so that headers are listed too: GCC lists every file
# a compile unit takes code or declarations from, so a header missing from all
# of them contributes nothing to the build. Files outside the source tree,
# e.g. the compiler's own headers, are left out.
#
# Needs readelf, from binutils, and, with --sbom, python3-apt and dpkg-deb.

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

# readelf --debug-dump=rawline prints, for each line program, a directory and
# a file name table; DWARF 5 numbers directories from 0 (the compilation
# directory), DWARF 4 from 1, with 0 implicitly being the compilation
# directory
TABLE_RE = re.compile(r"^ The (Directory|File Name) Table")
# DWARF 5 entries: "  1\t(indirect line string, offset: 0x9f): lib" or,
# depending on the DWARF forms, with the forms in columns of their own:
# "  0\t(udata)\t1\t(line_strp)\t(offset: 0xa978): main.c"; DWARF 4 entries:
# "  1\tlib" and "  1\t1\t0\t0\tts_kmp.c"
ENTRY_RE = re.compile(r"^  (\d+)\t(.*)$")
FORM_COLUMN_RE = re.compile(r"\([a-z0-9_]+\)")
FORM_RE = re.compile(r"^\([^)]*\): ")

KERNEL_IMAGE_RE = re.compile(r"linux-image-.*")
# the kernels the Debian security tracker covers: Debian's, whose images are
# built from linux-signed-<architecture>; see scripts/kernel-cves.py
DEBIAN_SOURCE_RE = re.compile(r"linux(?:-signed-.*)?")
SUFFIX = ".compiled-sources.txt"
NAME_RE = re.compile(r"[A-Za-z0-9.+~_-]+")


def is_debug_file(path):
    name = os.path.basename(path)
    return name.startswith("vmlinux") or ".ko" in name


def strip_form(value):
    return FORM_RE.sub("", value.strip())


def line_program_files(path):
    # (compilation directory or None, absolute or relative file path) of
    # every file name table entry
    output = subprocess.run(
        ["readelf", "--wide", "--debug-dump=rawline", path],
        check=True, capture_output=True, text=True, errors="replace").stdout
    table = None
    dirs = {}
    for line in output.splitlines():
        match = TABLE_RE.match(line)
        if match:
            table = match[1]
            if table == "Directory":
                dirs = {}
            continue
        match = ENTRY_RE.match(line)
        if not match or table is None:
            if not line.strip() or line.startswith("  Entry"):
                continue
            table = None
            continue
        index, rest = int(match[1]), match[2]
        fields = [field for field in rest.split("\t")
                  if not FORM_COLUMN_RE.fullmatch(field.strip())]
        if table == "Directory":
            dirs[index] = strip_form(fields[-1])
        else:
            name = strip_form(fields[-1])
            if name.startswith("<"):
                # e.g. <built-in>
                continue
            directory = dirs.get(int(fields[0]), "") if len(fields) > 1 \
                else ""
            yield dirs.get(0), os.path.join(directory, name)


def source_files(path, prefixes):
    files = set()
    for comp_dir, name in line_program_files(path):
        if os.path.isabs(name):
            for prefix in ([comp_dir] if comp_dir else []) + prefixes:
                if name.startswith(prefix.rstrip("/") + "/"):
                    name = os.path.relpath(name, prefix)
                    break
            else:
                # outside the source tree, e.g. the compiler's headers
                continue
        name = os.path.normpath(name)
        if not name.startswith(".."):
            files.add(name)
    return files


def find_debug_files(paths):
    debug_files = []
    for path in paths:
        if os.path.isdir(path):
            for root, _, names in os.walk(path):
                debug_files.extend(os.path.join(root, name)
                                   for name in names
                                   if is_debug_file(name))
        else:
            debug_files.append(path)
    return sorted(debug_files)


def compiled_sources(paths, prefixes, jobs):
    debug_files = find_debug_files(paths)
    if not debug_files:
        raise ValueError("no vmlinux or modules found")
    files = set()
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for result in pool.map(lambda path: source_files(path, prefixes),
                               debug_files):
            files |= result
    if not files:
        raise ValueError("no debug info found")
    return debug_files, files


def sbom_kernels(sbom):
    # {(source, version): (architecture, [linux-image-* packages])} of the
    # kernels of an SBOM, but Debian's
    kernels = {}
    for artifact in sbom.get("artifacts", []):
        if artifact.get("type") != "deb" or \
                not KERNEL_IMAGE_RE.fullmatch(artifact["name"]):
            continue
        metadata = artifact.get("metadata") or {}
        source = metadata.get("source") or artifact["name"]
        if DEBIAN_SOURCE_RE.fullmatch(source):
            continue
        key = (source, artifact["version"])
        architecture = metadata.get("architecture", "")
        kernels.setdefault(key, (architecture, []))[1].append(
            artifact["name"])
    return kernels


def apt_cache(root, sources, architecture):
    # a throwaway APT configuration, holding only the APT source of the
    # kernel: apt checks the signature of the repository, and the checksums
    # of what it downloads; python3-apt is only needed here
    import apt
    os.makedirs(os.path.join(root, "etc/apt/sources.list.d"))
    with open(os.path.join(root, "etc/apt/sources.list.d/kernel.sources"),
              "w", encoding="utf-8") as f:
        f.write(sources)
    with open(os.path.join(root, "etc/apt/apt.conf"), "w",
              encoding="utf-8") as f:
        f.write(f'APT::Architecture "{architecture}";\n'
                f'APT::Architectures "{architecture}";\n')
    cache = apt.Cache(rootdir=root)
    cache.update()
    cache.open()
    return cache


def dbg_version(cache, package, version):
    # the given version of a package's -dbg package, or None
    name = f"{package}-dbg"
    return cache[name].versions.get(version) if name in cache else None


def fetch_binary(version, directory):
    # without progress output, which would end up in the scan's reports
    import apt.progress.base
    return version.fetch_binary(directory,
                                progress=apt.progress.base.AcquireProgress())


def fetch(args):
    with open(args.sbom, encoding="utf-8") as f:
        sbom = json.load(f)
    kernels = sbom_kernels(sbom)
    if not kernels:
        print("no kernel to list the compiled sources of", file=sys.stderr)
        return 0
    if not os.path.exists(args.apt_sources):
        # e.g. the SBOM of an image built before it was recorded
        print(f"warning: no APT source of the kernel recorded, in "
              f"{args.apt_sources}; assessing by version only")
        return 0
    with open(args.apt_sources, encoding="utf-8") as f:
        sources = f.read()
    os.makedirs(args.output, exist_ok=True)
    caches = {}
    with tempfile.TemporaryDirectory() as tmp:
        for (source, version), (architecture, packages) in \
                sorted(kernels.items()):
            name = f"{source}_{version}"
            output = os.path.join(args.output, f"{name}{SUFFIX}")
            if not NAME_RE.fullmatch(name) or not architecture:
                print(f"warning: {name}: unexpected SBOM data, skipped")
                continue
            if os.path.exists(output):
                # from another image with the same kernel
                print(f"{name}: {output} already")
                continue
            work = tempfile.mkdtemp(dir=tmp)
            try:
                if architecture not in caches:
                    caches[architecture] = apt_cache(
                        os.path.join(tmp, f"apt-{architecture}"), sources,
                        architecture)
                dbg = [found for found in (
                           dbg_version(caches[architecture], package, version)
                           for package in sorted(packages)) if found]
                if not dbg:
                    print(f"warning: {name}: no linux-image-*-dbg package in "
                          "its APT source; assessing by version only")
                    continue
                deb = fetch_binary(dbg[0], work)
                subprocess.run(["dpkg-deb", "-x", deb,
                                os.path.join(work, "root")], check=True)
                debug_files, files = compiled_sources(
                    [os.path.join(work, "root")], args.prefix, args.jobs)
            except (OSError, ValueError, SystemError,
                    subprocess.CalledProcessError) as error:
                # python3-apt raises SystemError, and OSError when fetching
                detail = getattr(error, "stderr", None) or ""
                print(f"warning: {name}: cannot list the compiled sources: "
                      f"{error} {detail.strip()}; assessing by version only")
                continue
            finally:
                shutil.rmtree(work, ignore_errors=True)
            with open(output, "w", encoding="utf-8") as f:
                f.write("".join(f"{file}\n" for file in sorted(files)))
            print(f"{name}: {len(files)} source files, from "
                  f"{len(debug_files)} debug files of "
                  f"{dbg[0].package.name}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="List the kernel source files compiled into a kernel, "
                    "from the DWARF debug info of its vmlinux and modules.")
    parser.add_argument("paths", nargs="*",
                        help="vmlinux and module files with debug info, or "
                             "directories to search for them, e.g. an "
                             "unpacked linux-image-*-dbg package")
    parser.add_argument("--sbom",
                        help="Syft JSON SBOM of an image, to list the "
                             "compiled sources of its kernels instead")
    parser.add_argument("--apt-sources",
                        help="the APT source the image installed its kernel "
                             "from, from scripts/kernel-apt-sources.py, with "
                             "--sbom")
    parser.add_argument("--output",
                        help="directory to write the lists to, with --sbom")
    parser.add_argument("--prefix", action="append", default=[],
                        help="absolute path of the kernel source tree at "
                             "build time, when it isn't the compilation "
                             "directory; may be given more than once")
    parser.add_argument("--jobs", type=int, default=os.cpu_count(),
                        help="readelf processes to run in parallel")
    args = parser.parse_args()
    if bool(args.sbom) == bool(args.paths):
        parser.error("give either paths or --sbom")
    if args.sbom and not (args.output and args.apt_sources):
        parser.error("--sbom needs --output and --apt-sources")

    try:
        if args.sbom:
            return fetch(args)
        debug_files, files = compiled_sources(args.paths, args.prefix,
                                              args.jobs)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"{len(debug_files)} debug files, {len(files)} source files",
          file=sys.stderr)
    for name in sorted(files):
        print(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
