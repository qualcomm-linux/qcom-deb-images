#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# List the kernel source files compiled into a kernel build, from the DWARF
# debug info of its vmlinux and modules, as shipped by its linux-image-*-dbg
# package (the debug symbols; the -debug variants are kernels of their own,
# with -dbg packages of their own); one path per line, relative to the root of
# the kernel source tree, sorted, for kernel-cves.py.
#
# Either from debug files at hand, printing the list, or, with --sbom, for the
# kernels of an image, as kernellib.py tells them, like for
# kernel-cves.py: this downloads the -dbg package of each from the APT
# archive the image installed the kernel from, per the APT origins of the image
# (--apt-origins, from syft-apt-origins.py), and writes the list to
# --output as <source>_<version>.compiled-sources.txt. The download is with
# python3-apt, which checks the signature of the archive, with the key of the
# image's source of it, or of the source it's under, e.g. of the QLI repository
# for a snapshot of it, inline or from the keyring files of the image, which
# syft-apt-origins.py records too, and the checksum of the package;
# Check-Valid-Until is that of the source too. A kernel without -dbg package
# there, e.g. one removed from the archive by now, from no known archive, or of
# an image without APT origins, is only reported with a warning on stdout,
# prefixed with "warning:"; kernel-cves.py then assesses its CVEs by
# version alone. Debian's own kernel is left out, like there.
#
# The files are those debugedit lists, as rpm's find-debuginfo does for
# debugsource packages: the source and header files of the file name tables
# of the DWARF line programs, rather than only the compile units, so that
# headers are listed too. GCC lists every file a compile unit takes code or
# declarations from, so a header missing from all of them contributes nothing
# to the build. Files outside the source tree, e.g. the compiler's own
# headers, are left out; the source tree is the compilation directory of
# vmlinux, which the modules share, or the given --prefix.
#
# Needs readelf, from binutils, debugedit and, with --sbom, python3-apt,
# python3-debian and dpkg-deb.

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

import kernellib


def is_debug_file(path):
    name = os.path.basename(path)
    return name.startswith("vmlinux") or ".ko" in name


def compilation_dirs(path):
    # the compilation directories of the compile units of a debug file, from
    # readelf: e.g. "<17>   DW_AT_comp_dir    : (indirect line string,
    # offset: 0): /build/reproducible-path/linux-qcom-next-7.3.0", the value
    # after the last ": "
    output = subprocess.run(
        ["readelf", "--wide", "--debug-dump=info", "--dwarf-depth=1", path],
        check=True, capture_output=True, text=True, errors="replace").stdout
    return {line.rsplit(": ", 1)[-1].strip()
            for line in output.splitlines() if "DW_AT_comp_dir" in line}


def source_files(path, trees):
    # the source and header files of a debug file in the kernel source
    # trees, relative to them, as debugedit lists them: only those under its
    # base directory, so not e.g. the compiler's own headers; without a
    # directory to rewrite it to, it doesn't change the file
    files = set()
    with tempfile.TemporaryDirectory() as tmp:
        listing = os.path.join(tmp, "files")
        for tree in trees:
            subprocess.run(["debugedit", "--base-dir", tree, "--list-file",
                            listing, path],
                           check=True, capture_output=True, text=True)
            with open(listing, "rb") as f:
                names = f.read().decode(errors="replace").split("\0")
            for name in names:
                name = os.path.normpath(name) if name else ""
                # e.g. <built-in>
                if name and not name.startswith(("<", "..", "/")):
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
    # the kernel source tree: the compilation directory of vmlinux, which
    # the modules share, or the given ones
    vmlinux = [path for path in debug_files
               if os.path.basename(path).startswith("vmlinux")]
    trees = sorted(compilation_dirs((vmlinux or debug_files)[0])
                   | set(prefixes))
    if not trees:
        raise ValueError("no compilation directory in the debug info")
    files = set()
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for result in pool.map(lambda path: source_files(path, trees),
                               debug_files):
            files |= result
    if not files:
        raise ValueError("no debug info found")
    return debug_files, files


def archive_source(archive, origins, keyring_dir):
    # the APT source stanza to download from an archive with, of the
    # syft-apt-origins.py JSON: with the key of the image's source of the
    # archive, or of the source it's under, e.g. of the QLI repository for a
    # snapshot of it, which the image recipe derives from that source. Its
    # keyring files, of the JSON too, are written to keyring_dir. None
    # without such a source, or a key
    from debian import deb822
    sources = origins.get("sources", {})
    uri = archive["uri"]
    under = [source for source in sources
             if uri == source or uri.startswith(source + "/")]
    if not under:
        return None
    source_uri = max(under, key=len)
    source = deb822.Deb822(sources[source_uri])
    signed_by = source.get("Signed-By", "")
    if "-----BEGIN PGP PUBLIC KEY BLOCK-----" not in signed_by:
        # paths of keyring files and fingerprints
        keys = []
        for key in signed_by.replace(",", " ").split():
            if key.startswith("/"):
                if key not in origins.get("keyrings", {}):
                    return None
                data = base64.b64decode(origins["keyrings"][key])
                # apt tells the format from the extension, e.g. .asc
                path = os.path.join(
                    keyring_dir, hashlib.sha256(data).hexdigest()
                    + os.path.splitext(key)[1])
                with open(path, "wb") as f:
                    f.write(data)
                key = path
            keys.append(key)
        if not any(key.startswith("/") for key in keys):
            return None
        signed_by = " ".join(keys)
    stanza = deb822.Deb822()
    stanza["Types"] = "deb"
    stanza["URIs"] = uri
    stanza["Suites"] = archive["suite"]
    stanza["Components"] = archive["component"]
    stanza["Signed-By"] = signed_by
    # as the image's source has it, e.g. set up by the image recipe
    if "Check-Valid-Until" in source:
        stanza["Check-Valid-Until"] = source["Check-Valid-Until"]
    return stanza.dump()


def apt_cache(root, sources, architecture):
    # a throwaway APT configuration, holding only the APT source of the
    # kernel: apt checks the signature of the archive, and the checksums of
    # what it downloads; python3-apt is only needed here
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


def sbom_kernels(args):
    # {(source, version): (architecture, [kernel image packages], [archives
    # to download from])} of the kernels of the image to list the compiled
    # sources of, and the APT origins of the image
    with open(args.sbom, encoding="utf-8") as f:
        kernels = kernellib.sbom_kernels(json.load(f))
    with open(args.apt_origins, encoding="utf-8") as f:
        origins = json.load(f)
    return {key: (architecture, kernels[key][0], archives)
            for key, (architecture, archives)
            in kernellib.non_debian_kernels(kernels, origins).items()}, \
        origins


def fetch(args):
    if not os.path.exists(args.apt_origins):
        # e.g. the SBOM of an image built before they were recorded
        print(f"warning: no APT origins of the image, {args.apt_origins}; "
              "assessing its kernel CVEs by version only")
        return 0
    kernels, origins = sbom_kernels(args)
    if not kernels:
        print("no non-Debian kernel to list the compiled sources of",
              file=sys.stderr)
        return 0
    os.makedirs(args.output, exist_ok=True)
    caches = {}
    with tempfile.TemporaryDirectory() as tmp:
        keyring_dir = os.path.join(tmp, "keyrings")
        os.mkdir(keyring_dir)
        for (source, version), (architecture, packages, archives) in \
                sorted(kernels.items()):
            name = f"{source}_{version}"
            output = os.path.join(
                args.output, f"{name}{kernellib.COMPILED_SOURCES_SUFFIX}")
            # SBOM data, in a file name and the APT configuration
            if "/" in name or name.startswith(".") or \
                    not architecture.isalnum():
                print(f"warning: {name}: unexpected SBOM data, skipped")
                continue
            if os.path.exists(output):
                # from another image with the same kernel
                print(f"{name}: {output} already")
                continue
            if not archives:
                print(f"warning: {name}: from no known APT archive, no -dbg "
                      "package to download; assessing by version only")
                continue
            stanzas = [stanza for stanza in (
                archive_source(archive, origins, keyring_dir)
                for archive in archives)
                if stanza]
            if not stanzas:
                print(f"warning: {name}: no APT archive with a key to "
                      "download its -dbg package from; assessing by version "
                      "only")
                continue
            work = tempfile.mkdtemp(dir=tmp)
            try:
                dbg = []
                for stanza in stanzas:
                    if (stanza, architecture) not in caches:
                        caches[(stanza, architecture)] = apt_cache(
                            os.path.join(tmp, f"apt-{len(caches)}"), stanza,
                            architecture)
                    cache = caches[(stanza, architecture)]
                    dbg = [found for found in (
                               dbg_version(cache, package, version)
                               for package in sorted(packages)) if found]
                    if dbg:
                        break
                if not dbg:
                    print(f"warning: {name}: no -dbg package of "
                          f"{', '.join(packages)} in its APT archive; "
                          "assessing by version only")
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
    parser.add_argument("--apt-origins",
                        help="the APT origins of the image, from "
                             "syft-apt-origins.py, with --sbom")
    parser.add_argument("--output",
                        help="directory to write the lists to, with --sbom")
    parser.add_argument("--prefix", action="append", default=[],
                        help="absolute path of the kernel source tree at "
                             "build time, when it isn't the compilation "
                             "directory; may be given more than once")
    parser.add_argument("--jobs", type=int, default=os.cpu_count(),
                        help="debugedit processes to run in parallel")
    args = parser.parse_args()
    if bool(args.sbom) == bool(args.paths):
        parser.error("give either paths or --sbom")
    if args.sbom and not (args.output and args.apt_origins):
        parser.error("--sbom needs --output and --apt-origins")

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
