#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# input is a Syft JSON file as the first argument and the scanned root
# filesystem as the second argument; output is a JSON document listing each
# Debian package of the SBOM, with its source package and the APT archives
# providing that exact version, by the APT lists of the root filesystem, e.g.:
#
# {"packages": [
#   {"package": "linux-image-qcom-next",
#    "version": "7.3.0+git20261030~gae2606baf50f-0qli1~bpo13+1",
#    "architecture": "arm64", "source": "linux-qcom-next",
#    "source_version": "7.3.0+git20261030~gae2606baf50f-0qli1~bpo13+1",
#    "archives": [{"vendor": "Qualcomm",
#                  "uri": "https://deb.debusine.qualcomm.com/qualcomm/qli",
#                  "suite": "trixie", "component": "main"}]},
#   ...],
#  "sources": {
#   "https://deb.debusine.qualcomm.com/qualcomm/qli":
#    "Types: deb\nURIs: https://deb.debusine.qualcomm.com/qualcomm/qli\n...",
#   ...},
#  "keyrings": {
#   "/usr/share/keyrings/debian-archive-keyring.gpg": "mQINBF...",
#   ...}}
#
# along with the deb source stanzas of the image's
# /etc/apt/sources.list.d/*.sources, enabled or not, as apt reads them, e.g.
# with their Signed-By key inline, keyed by their URIs, and the keyring files
# their Signed-By names, base64-encoded, keyed by their paths, for tools to
# download more from an archive, e.g. the -dbg package of the kernel. Local
# sources (file:) are left out, there's nothing to download from them.
#
# dpkg doesn't record which archive a package was installed from, so an
# archive providing the installed version is the best there is; a package
# can have several, e.g. trixie and trixie-security, or none, e.g. those of
# the local APT repository the image recipe removes again before the image is
# done, along with its lists.
#
# The lists are read as they are, not through apt's configuration: they are
# those of the last APT update of the image build, while the configured
# sources may have changed since, e.g. snapshot builds remove their snapshot
# sources again at the end. The URI, suite and component of a list come from
# its file name, which apt builds from them, e.g.
# deb.debian.org_debian_dists_trixie_main_binary-arm64_Packages; apt escapes
# "_" in them, so it splits up again. Only the scheme of the URI is lost,
# and any credentials: it's that of the source stanza of
# /etc/apt/sources.list.d/*.sources with that URI, enabled or not, and https
# without one, e.g. for the snapshot archives, which all the known hosts serve.
#
# The vendor of an archive comes from the host of its URI, see VENDORS; the
# Origin of its Release file, which whoever publishes the archive sets, has to
# start with it. An archive of another host, or with another Origin, is an
# error, so that packages don't get attributed to the wrong vendor and new
# archives get reviewed and added explicitly. So is an SBOM none of whose
# packages any archive provides, as that means the APT lists are missing or
# don't match the SBOM.
#
# Needs python3-apt and python3-debian.

import argparse
import base64
import json
import os
import sys
from collections import defaultdict
from urllib.parse import unquote, urlsplit

import apt_pkg
from debian import deb822

# the vendors of the archives the images are built from, by host
VENDORS = {
    "deb.debian.org": "Debian",
    "security.debian.org": "Debian",
    "snapshot.debian.org": "Debian",
    "deb.debusine.qualcomm.com": "Qualcomm",
}


class OriginError(Exception):
    pass


def load_syft_json(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def source_stanzas(rootfs):
    # {URI: stanza} of the deb sources of /etc/apt/sources.list.d, enabled or
    # not, by their URIs without credentials, e.g.
    # {"http://deb.debian.org/debian": Deb822(...)}; the first stanza of a URI
    directory = os.path.join(rootfs, "etc/apt/sources.list.d")
    names = sorted(name for name in os.listdir(directory)
                   if name.endswith(".sources")) \
        if os.path.isdir(directory) else []
    stanzas = {}
    for name in names:
        with open(os.path.join(directory, name), encoding="utf-8") as f:
            for fields in deb822.Deb822.iter_paragraphs(f):
                if "deb" not in fields.get("Types", "").split():
                    continue
                for uri in fields.get("URIs", "").split():
                    parts = urlsplit(uri.rstrip("/"))
                    uri = parts._replace(
                        netloc=parts.netloc.rsplit("@", 1)[-1]).geturl()
                    stanzas.setdefault(uri, fields)
    return stanzas


def rootfs_file(rootfs, path):
    # the path of a file of the root filesystem, following its symbolic
    # links within it, rather than those of the host
    parts = [part for part in path.split("/") if part]
    resolved = ""
    links = 0
    while parts:
        part = parts.pop(0)
        if part == ".":
            continue
        if part == "..":
            resolved = os.path.dirname(resolved)
            continue
        candidate = f"{resolved}/{part}"
        if os.path.islink(rootfs + candidate):
            links += 1
            if links > 40:
                raise OSError(f"{path}: too many levels of symbolic links")
            target = os.readlink(rootfs + candidate)
            if target.startswith("/"):
                resolved = ""
            parts = [part for part in target.split("/") if part] + parts
            continue
        resolved = candidate
    return rootfs + resolved


def source_keyrings(rootfs, stanzas):
    # {path: base64 contents} of the keyring files the Signed-By of the
    # stanzas names, rather than holding the key inline; Signed-By also takes
    # fingerprints, and a list of those and paths
    keyrings = {}
    for stanza in stanzas:
        signed_by = stanza.get("Signed-By", "")
        if "-----BEGIN PGP PUBLIC KEY BLOCK-----" in signed_by:
            continue
        for path in signed_by.replace(",", " ").split():
            if not path.startswith("/") or path in keyrings:
                continue
            try:
                with open(rootfs_file(rootfs, path), "rb") as f:
                    keyrings[path] = base64.b64encode(f.read()).decode()
            except OSError as error:
                print(f"warning: keyring of an APT source: {error}",
                      file=sys.stderr)
    return keyrings


def list_archive(name):
    # (list file name prefix of the URI, the URI without scheme, suite,
    # component) of the file name of a Packages list, e.g.
    # ("deb.debian.org_debian_", "deb.debian.org/debian", "trixie", "main") of
    # deb.debian.org_debian_dists_trixie_main_binary-arm64_Packages; None for
    # those of flat repositories, which have no suite and component
    parts = name.split("_")
    if len(parts) < 6 or parts[-5] != "dists" or \
            not parts[-2].startswith("binary-") or parts[-1] != "Packages":
        return None
    return ("_".join(parts[:-5]) + "_",
            "/".join(unquote(part) for part in parts[:-5]),
            unquote(parts[-4]), unquote(parts[-3]))


def release_origin(lists_dir, prefix):
    # the Origin of the (In)Release file of an archive, or None without one;
    # apt's own reading, without the signature of InRelease
    for suffix in ("InRelease", "Release"):
        path = os.path.join(lists_dir, prefix + suffix)
        if os.path.exists(path):
            with os.fdopen(apt_pkg.open_maybe_clear_signed_file(path)) as f, \
                    apt_pkg.TagFile(f) as tag_file:
                return next(iter(tag_file), {}).get("Origin", "")
    return None


def archive_vendor(uri, origin, path):
    vendor = VENDORS.get(urlsplit(uri).hostname or "")
    if vendor is None:
        raise OriginError(f"{path}: unknown archive host of {uri}, expected "
                          f"one of {', '.join(sorted(VENDORS))}")
    if origin.split(" ", 1)[0] != vendor:
        raise OriginError(f"{path}: Origin {origin!r} doesn't match the "
                          f"vendor {vendor!r} of {uri}")
    return vendor


def archive_packages(rootfs, stanzas):
    # (package, version, architecture): the archives listing it
    lists_dir = os.path.join(rootfs, "var/lib/apt/lists")
    # the URIs of the sources, by their list file name prefix, e.g.
    # {"deb.debian.org_debian_": "http://deb.debian.org/debian"}
    uris = {apt_pkg.uri_to_filename(uri + "/"): uri for uri in stanzas}
    packages = defaultdict(list)
    for name in sorted(os.listdir(lists_dir)):
        if not name.endswith("_Packages"):
            continue
        archive = list_archive(name)
        if archive is None:
            print(f"warning: {name}: not the list of an archive with suites "
                  "and components, skipped", file=sys.stderr)
            continue
        uri_prefix, location, suite, component = archive
        uri = uris.get(uri_prefix, f"https://{location}")
        # the (In)Release file of the suite, e.g.
        # deb.debian.org_debian_dists_trixie_InRelease
        prefix = name.rsplit("_", 3)[0] + "_"
        origin = release_origin(lists_dir, prefix)
        if origin is None:
            raise OriginError(f"{prefix}: no Release file in {lists_dir}")
        archive = {"vendor": archive_vendor(uri, origin, prefix),
                   "uri": uri, "suite": suite, "component": component}
        with apt_pkg.TagFile(os.path.join(lists_dir, name)) as tag_file:
            for fields in tag_file:
                key = (fields.get("Package"), fields.get("Version"),
                       fields.get("Architecture"))
                if archive not in packages[key]:
                    packages[key].append(archive)
    for archives in packages.values():
        archives.sort(key=lambda a: (a["uri"], a["suite"], a["component"]))
    return packages


def package_origins(data, packages):
    rows = []
    for artifact in data.get("artifacts", []):
        if artifact.get("type") != "deb":
            continue
        metadata = artifact.get("metadata") or {}
        binary = metadata.get("package") or artifact.get("name")
        version = metadata.get("version") or artifact.get("version", "")
        arch = metadata.get("architecture", "")
        rows.append({
            "package": binary,
            "version": version,
            "architecture": arch,
            "source": metadata.get("source") or binary,
            "source_version": metadata.get("sourceVersion") or version,
            "archives": packages.get((binary, version, arch), []),
        })
    return sorted(rows, key=lambda row: (row["package"], row["version"],
                                         row["architecture"]))


def main():
    parser = argparse.ArgumentParser(
        description="List the APT archives of Syft packages.")
    parser.add_argument("syft_json", help="Path to the Syft JSON file")
    parser.add_argument("rootfs",
                        help="Path to the scanned root filesystem")
    args = parser.parse_args()
    apt_pkg.init_config()
    try:
        syft_data = load_syft_json(args.syft_json)
        stanzas = source_stanzas(args.rootfs)
        rows = package_origins(syft_data,
                               archive_packages(args.rootfs, stanzas))
    except (OSError, ValueError, SystemError, OriginError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if not any(row["archives"] for row in rows):
        print("error: no package found in the APT lists", file=sys.stderr)
        return 1
    stanzas = {uri: stanza for uri, stanza in sorted(stanzas.items())
               if urlsplit(uri).scheme != "file"}
    json.dump({"packages": rows,
               "sources": {uri: stanza.dump()
                           for uri, stanza in stanzas.items()},
               "keyrings": source_keyrings(args.rootfs, stanzas.values())},
              sys.stdout, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
