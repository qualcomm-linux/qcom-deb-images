#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

# Generate the per-board flash directories for
# debos-recipes/qualcomm-linux-debian-flash.yaml.
#
# Board definitions are read from a YAML file `boards.yaml` and only the
# boards requested with `--target-boards` have their boot binaries
# and CDT downloaded and unpacked and only their SoCs get a multi-DTB FIT image
# built.
#
# For each selected board this generates one `flash_<board>_<storage>/`
# directory per qcom-ptool platform containing the ptool partition/flashing
# files, the silicon family boot binaries, the CDT and the device tree.

import argparse
import glob
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

DOWNLOAD_RETRIES = 3
# read/write chunk size when hashing and downloading
CHUNK = 1024 * 1024


def log(msg):
    print(msg, flush=True)


def warn(msg):
    print(msg, file=sys.stderr, flush=True)


def run(cmd, **kwargs):
    """Run a command, echoing it first; raises on a non-zero exit."""
    log("+ " + " ".join(str(c) for c in cmd))
    return subprocess.run([str(c) for c in cmd], check=True, **kwargs)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url, dest, sha256sum, description):
    """Download url to dest and verify its SHA256 sum.

    A dest that is already present with the expected sum is kept, so a rerun
    against an existing work directory doesn't refetch.
    """
    # urlopen by default allows many protocol schemes (e.g. `file://`) which
    # could be misused by bad actors. Only allow `https://` protocol schemes.
    scheme = urllib.parse.urlsplit(url).scheme
    if scheme != "https":
        raise SystemExit(
            f"error: {description}: refusing to fetch "
            f"{scheme or 'schemeless'} URL, only https is allowed: {url}"
        )

    if dest.exists() and sha256_file(dest) == sha256sum:
        log(f"Already downloaded: {description} ({dest.name})")
        return

    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        log(f"Downloading {description}: {url}")
        try:
            # url is audited already: it comes from boards.yaml, is https
            # only and the payload is validated against the expect checksum.
            with (
                # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected  # noqa: E501
                urllib.request.urlopen(url, timeout=60) as response,
                open(dest, "wb") as out,
            ):
                shutil.copyfileobj(response, out, CHUNK)
        except OSError as e:
            # the fetch happens inside the debos fakemachine, so a transient
            # network failure here shouldn't fail the whole recipe
            if attempt == DOWNLOAD_RETRIES:
                raise
            warn(
                f"Download of {description} failed ({e}), retrying "
                f"({attempt}/{DOWNLOAD_RETRIES - 1})"
            )
            continue

        actual = sha256_file(dest)
        if actual != sha256sum:
            raise RuntimeError(
                f"SHA256 mismatch for {description} ({dest.name}): "
                f"expected {sha256sum}, got {actual}"
            )
        log(
            f"Downloaded and verified {description} "
            f"({dest.stat().st_size} bytes)"
        )
        return


def unpack(archive, dest):
    """Unpack a .zip or .tar.gz archive into dest.

    unzip/tar are used rather than the zipfile/tarfile modules as the boot
    binaries ship executables and zipfile does not restore unix permissions.
    """
    dest.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith((".tar.gz", ".tgz")):
        run(["tar", "-xzf", archive, "-C", dest])
    else:
        run(["unzip", "-q", archive, "-d", dest])


def unpack_boot_binaries(archive, dest):
    """Unpack boot binaries, stripping the archive's top level directories."""
    unpack_dir = dest / "unpack"
    unpack(archive, unpack_dir)
    # strip top directories
    for top in sorted(unpack_dir.glob("*")):
        if not top.is_dir():
            raise RuntimeError(
                f"{archive.name}: expected the archive to hold only top level "
                f"directories, found {top.name}"
            )
        for entry in sorted(top.glob("*")):
            shutil.move(str(entry), str(dest / entry.name))
        top.rmdir()
    unpack_dir.rmdir()


def load_boards(path):
    with open(path) as f:
        boards = yaml.safe_load(f)["boards"]
    for board in boards:
        board.setdefault("dtb_bin_type", "combineddtb")
    return boards


def board_u_boot_file(board, template_vars):
    """Path of the board's U-Boot boot image, or None if it needs none.

    Boards with u_boot_file_var take their boot image from a debos template
    variable (RB1 uses a locally built U-Boot); they cannot be built
    without it.
    """
    var = board.get("u_boot_file_var")
    if var is None:
        return None
    return template_vars.get(var)


def select_boards(boards, target_boards, template_vars):
    """Filter boards down to --target-boards, rejecting unusable names."""
    available = []
    for board in boards:
        var = board.get("u_boot_file_var")
        if var is not None and not template_vars.get(var):
            log(
                f"Board {board['name']} is unavailable: "
                f"-t {var}:<path> not set"
            )
            continue
        available.append(board)

    if target_boards == "all":
        return available

    known = {board["name"] for board in boards}
    names = {name.strip() for name in target_boards.split(",")}
    names.discard("")

    available_names = [board["name"] for board in available]
    for name in sorted(names):
        if name in available_names:
            continue
        if name in known:
            # named a real board that this invocation cannot build; say why
            # rather than claiming the board doesn't exist
            var = next(
                b["u_boot_file_var"] for b in boards if b["name"] == name
            )
            raise SystemExit(
                f"error: target_boards: {name} requires -t {var}:<path>"
            )
        raise SystemExit(
            f"error: target_boards: {name} is not a known board; "
            f"available boards: {', '.join(available_names)}"
        )

    return [board for board in available if board["name"] in names]


def dtbs_in_tarball(dtbs_tarball):
    """Names of the .dtb files shipped in dtbs.tar.gz."""
    if not dtbs_tarball.exists():
        return set()
    with tarfile.open(dtbs_tarball, "r:gz") as tar:
        return {name for name in tar.getnames() if name.endswith(".dtb")}


def build_fit_images(
    boards,
    available_dtbs,
    dtbs_tarball,
    qcom_dtb_metadata,
    work_dir,
    artifact_dir,
):
    """Generate one `dtb-multidtb-<soc>.bin` multi-DTB FIT image per SoC.

    A single combined FIT across all SoCs overflows the 4 MiB FAT container, so
    each SoC gets its own; --soc filters the FIT down to just that SoC's device
    trees. Only boards with dtb_bin_type multidtb need one; building a FIT for
    any other SoC would be wasted work. qcm2290 has no /soc entry in
    qcom-dtb-metadata yet and only appears on combineddtb boards anyway:
    https://github.com/qualcomm-linux/qcom-dtb-metadata/issues/126
    """
    if not dtbs_tarball.exists():
        return

    multidtb = [b for b in boards if b["dtb_bin_type"] == "multidtb"]
    if not multidtb:
        return

    dtbs_dir = work_dir / "dtbs"
    dtbs_dir.mkdir(parents=True, exist_ok=True)
    # unpack only qcom/ DTBs
    run(["tar", "-C", dtbs_dir, "-xzf", dtbs_tarball, "qcom"])

    # preserve the board order of the YAML, one entry per SoC
    socs = list(dict.fromkeys(board["soc_id"] for board in multidtb))
    for soc in socs:
        # Only build a FIT for this SoC if at least one of its boards' DTBs is
        # actually shipped in dtbs.tar.gz. qcom-dtb-metadata may know a new
        # SoC (so --soc is valid and its ITS configs get selected) before the
        # kernel ships that SoC's DTBs; without this gate build-dtb-image.sh
        # --prune would drop every config and hard-fail. This mirrors the
        # per-board "dtb not available" skip below.
        soc_dtbs = [b["dtb"] for b in multidtb if b["soc_id"] == soc]
        if not any(dtb in available_dtbs for dtb in soc_dtbs):
            log(f"Skipping FIT for SoC {soc}: no DTBs present in dtbs.tar.gz")
            continue

        dtb_bin = artifact_dir / f"dtb-multidtb-{soc}.bin"
        dtb_bin.unlink(missing_ok=True)
        try:
            run(
                [
                    qcom_dtb_metadata / "build-dtb-image.sh",
                    "--dtb-src",
                    dtbs_dir / "qcom",
                    "--soc",
                    soc,
                    "--size",
                    "2",
                    "--out",
                    dtb_bin,
                    "--prune",
                ]
            )
        except subprocess.CalledProcessError:
            # build-dtb-image.sh --prune fails when every ITS config for this
            # SoC references a DTB/DTBO the kernel doesn't ship yet (all
            # configs get pruned). This happens even though the SoC's base
            # DTB is present, e.g. when all of a SoC's configs additionally
            # require a .dtbo overlay that isn't built. Treat that as "not
            # ready yet": warn, drop any partial output and skip this SoC's
            # FIT rather than failing the whole recipe.
            dtb_bin.unlink(missing_ok=True)
            log(
                f"Skipping FIT for SoC {soc}: build-dtb-image.sh --prune left "
                f"no usable configs (required DTBs/DTBOs not in dtbs.tar.gz)"
            )
        else:
            log(f"Multi-DTB FIT image generated: {dtb_bin}")


# boot binary files to copy into a flash directory: these shouldn't ship
# partition files, but make sure not to accidentally clobber any such file
BOOT_BINARY_EXCLUDES = (
    "gpt_*",
    "patch*.xml",
    "rawprogram*.xml",
    "wipe*.xml",
    "zeros_*",
)
BOOT_BINARY_INCLUDES = (
    "LICENSE",
    "Qualcomm-Technologies-Inc.-Proprietary",
    "prog_*",
    "boot.img",
    "*.bin",
    "*.elf",
    "*.melf",
    "*.fv",
    "*.mbn",
)


def copy_boot_binaries(boot_binaries_dir, flash_dir):
    """Copy the silicon family boot binaries into the flash directory."""
    for root, dirs, files in os.walk(boot_binaries_dir):
        dirs.sort()
        for name in sorted(files):
            path = Path(root) / name
            if any(path.match(pattern) for pattern in BOOT_BINARY_EXCLUDES):
                continue
            if not any(
                path.match(pattern) for pattern in BOOT_BINARY_INCLUDES
            ):
                continue
            log(f"'{path}' -> '{flash_dir / name}'")
            shutil.copy2(path, flash_dir / name)


def generate_flash_dir(board, platform, context):
    """Generate one flash_<board>_<storage>/ directory for a ptool platform."""
    name = board["name"]
    work_dir = context["work_dir"]
    artifact_dir = context["artifact_dir"]

    # infer storage from ptool platform dir; first strip leading directory,
    # then strip trailing use case (after first dash)
    storage_use_case = platform.split("/", 1)[1]
    disk_type = storage_use_case.split("-", 1)[0]

    cdt_file = board.get("cdt", {}).get("file", "")

    # generate ptool files - various XML files for flashing, GPT data etc.
    ptool_dir = work_dir / "ptool" / platform
    ptool_dir.mkdir(parents=True, exist_ok=True)
    run(
        [
            context["scripts_dir"] / "gen-ptool.sh",
            context["qcom_ptool"],
            platform,
            cdt_file,
            context["buildid"],
            disk_type,
            board["dtb_bin_type"],
            board["soc_id"],
        ],
        cwd=ptool_dir,
    )

    # create board-specific flash directory
    flash_dir = artifact_dir / f"flash_{name}_{disk_type}"
    shutil.rmtree(flash_dir, ignore_errors=True)
    flash_dir.mkdir()
    log(f"created directory '{flash_dir}'")
    # copy platform partition files
    for entry in sorted(ptool_dir.glob("*")):
        shutil.copy2(entry, flash_dir / entry.name)

    # remove BLANK_GPT, WIPE_PARTITIONS and wipe_rawprogram files as it's
    # common for people to run "qdl rawprogram*.xml" or pcat, mistakingly
    # including these; perhaps ptool should have a flag not to generate these
    for pattern in (
        "rawprogram*_BLANK_GPT.xml",
        "rawprogram*_WIPE_PARTITIONS.xml",
        "wipe_rawprogram*.xml",
    ):
        removed = sorted(flash_dir.glob(pattern))
        if not removed:
            raise RuntimeError(
                f"{flash_dir}: no file matching {pattern} to remove"
            )
        for entry in removed:
            log(f"removed '{entry}'")
            entry.unlink()

    boot_binaries_dir = work_dir / f"{name}_boot-binaries"
    copy_boot_binaries(boot_binaries_dir, flash_dir)

    # copy the sail_nor directory (standalone rawprogram/gpt/firehose/elf
    # payloads) for SAIL domain flashing on SPI-NOR; only some silicon families
    # ship it
    sail_nor_dir = boot_binaries_dir / "sail_nor"
    if sail_nor_dir.is_dir():
        shutil.copytree(
            sail_nor_dir,
            flash_dir / "sail_nor",
            symlinks=True,
            copy_function=shutil.copy2,
            dirs_exist_ok=True,
        )

    u_boot_file = board_u_boot_file(board, context["template_vars"])
    if u_boot_file:
        # copy U-Boot binary to boot.img; qcom-ptool partitions.conf files use
        # filename=boot.img for boot_a and boot_b partitions
        shutil.copy2(artifact_dir / u_boot_file, flash_dir / "boot.img")

    if cdt_file:
        # copy just the CDT data; no partition or flashing files
        shutil.copy2(
            work_dir / f"{name}_cdt" / cdt_file,
            flash_dir / Path(cdt_file).name,
        )

    if board["dtb_bin_type"] == "combineddtb":
        # generate a dtb-combineddtb.bin FAT partition with just a single dtb
        # for the current board; long-term this should really be a set of dtbs
        # and overlays as to share dtb.bin across boards
        dtb_bin = flash_dir / "dtb-combineddtb.bin"
        dtb_bin.unlink(missing_ok=True)
        # dtb-combineddtb.bin is only used in UFS based boards at the moment
        # and UFS uses a 4k sector size, so pass -S 4096 in
        # qcom-ptool/platforms/*/partitions.conf, dtb_a and _b partitions are
        # provisioned with 64MiB; create a 4MiB FAT that will comfortably fit
        # in these and hold the target device tree, which is 4096 KiB sized
        # blocks for mkfs.vfat's last argument
        run(["mkfs.vfat", "-S", "4096", "-C", dtb_bin, "4096"])
        # extract board device tree from the root filesystem provided tarball
        run(
            [
                "tar",
                "-C",
                work_dir,
                "-xf",
                context["dtbs_tarball"],
                board["dtb"],
            ]
        )
        # copy into the FAT as combined-dtb.dtb
        run(
            [
                "mcopy",
                "-mp",
                "-i",
                dtb_bin,
                work_dir / board["dtb"],
                "::/combined-dtb.dtb",
            ]
        )

    return flash_dir


def merge_spinor_dirs(boards, context):
    """Copy each spinor flash dir into its parent storage flash dirs.

    Storage types are read from product_info/chipid in contents.xml.
    """
    artifact_dir = context["artifact_dir"]
    scripts_dir = context["scripts_dir"]

    for board in boards:
        name = board["name"]
        spinor_dir = artifact_dir / f"flash_{name}_spinor"
        contents_xml = spinor_dir / "contents.xml"
        if not contents_xml.is_file():
            continue

        storage_types = subprocess.run(
            [
                "xmlstarlet",
                "sel",
                "-t",
                "-m",
                "//product_info/chipid",
                "-v",
                "@storage_type",
                "-n",
                contents_xml,
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        storages = sorted(
            {
                s.strip().lower()
                for s in storage_types.splitlines()
                if s.strip()
            }
        )

        for storage in storages:
            if storage == "spinor":
                continue
            target_dir = artifact_dir / f"flash_{name}_{storage}"
            if not target_dir.is_dir():
                continue
            shutil.copytree(
                spinor_dir,
                target_dir / "spinor",
                symlinks=True,
                copy_function=shutil.copy2,
                dirs_exist_ok=True,
            )

            rawprogram = target_dir / "rawprogram0.xml"
            efi_img = subprocess.run(
                [
                    scripts_dir / "get-rawprogram-filename.py",
                    "efi",
                    rawprogram,
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            rootfs_img = subprocess.run(
                [
                    scripts_dir / "get-rawprogram-filename.py",
                    "rootfs",
                    rawprogram,
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            # fix file_path (../->../../) then rename efi.bin/rootfs.img
            copied_contents = target_dir / "spinor" / "contents.xml"
            if copied_contents.is_file():
                run(
                    [
                        "xmlstarlet",
                        "ed",
                        "-L",
                        "-u",
                        (
                            f"//*[@storage_type='{storage}']"
                            "[file_name/text()='efi.bin']"
                            "/file_path[text()='../']"
                        ),
                        "-v",
                        "../../",
                        "-u",
                        (
                            f"//*[@storage_type='{storage}']"
                            "/file_name[text()='efi.bin']"
                        ),
                        "-v",
                        efi_img,
                        "-u",
                        (
                            f"//*[@storage_type='{storage}']"
                            "[file_name/text()='rootfs.img']"
                            "/file_path[text()='../']"
                        ),
                        "-v",
                        "../../",
                        "-u",
                        (
                            f"//*[@storage_type='{storage}']"
                            "/file_name[text()='rootfs.img']"
                        ),
                        "-v",
                        rootfs_img,
                        "-u",
                        (
                            "//download_file"
                            "[starts-with(file_name/text(),'dtb-multidtb-')]"
                            "/file_path[text()='..']"
                        ),
                        "-v",
                        "../../",
                        copied_contents,
                    ]
                )

            # fix rawprogram*.xml in spinor dir:
            # ../dtb-multidtb-<soc>.bin -> ../../dtb-multidtb-<soc>.bin;
            # preserve the per-SoC filename by rewriting only the prefix
            for xml in sorted(
                (target_dir / "spinor").rglob("rawprogram*.xml")
            ):
                run(
                    [
                        "xmlstarlet",
                        "ed",
                        "-L",
                        "-u",
                        (
                            "//program"
                            "[starts-with(@filename,'../dtb-multidtb-')]"
                            "/@filename"
                        ),
                        "-x",
                        "concat('../', .)",
                        xml,
                    ]
                )


def unpacked_tarball(download_dir, name):
    """Path of a tarball unpacked by debos' download action."""
    matches = glob.glob(str(download_dir / f"{name}.tar.gz.d" / f"{name}-*"))
    if len(matches) != 1:
        raise SystemExit(
            f"error: expected exactly one unpacked {name} tree, "
            f"found {matches}"
        )
    return Path(matches[0])


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate per-board flash directories for the flash "
        "recipe."
    )
    parser.add_argument(
        "--boards",
        type=Path,
        required=True,
        help="YAML file with the board definitions",
    )
    parser.add_argument(
        "--download-dir",
        type=Path,
        required=True,
        help="directory holding the debos downloads",
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=os.environ.get("ARTIFACTDIR"),
        help="debos artifact directory (default: $ARTIFACTDIR)",
    )
    parser.add_argument(
        "--scripts-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="directory holding the helper scripts",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path("build"),
        help="work directory, removed on success",
    )
    parser.add_argument(
        "--target-boards",
        default="all",
        help="comma separated board names to build, or 'all'",
    )
    parser.add_argument(
        "--buildid",
        default="",
        help="build id recorded in the generated contents.xml",
    )
    parser.add_argument(
        "--template-var",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="debos template variable a board may depend on",
    )
    args = parser.parse_args()

    if args.artifact_dir is None:
        parser.error("--artifact-dir is required when $ARTIFACTDIR is unset")

    args.template_vars = {}
    for item in args.template_var:
        name, _, value = item.partition("=")
        args.template_vars[name] = value

    return args


def main():
    args = parse_args()

    boards = load_boards(args.boards)
    boards = select_boards(boards, args.target_boards, args.template_vars)
    if not boards:
        raise SystemExit("error: no boards selected")
    log("Selected boards: " + ", ".join(board["name"] for board in boards))

    work_dir = args.work_dir
    work_dir.mkdir(parents=True)
    downloads = work_dir / "downloads"
    downloads.mkdir()

    artifact_dir = args.artifact_dir.resolve()
    dtbs_tarball = artifact_dir / "dtbs.tar.gz"
    available_dtbs = dtbs_in_tarball(dtbs_tarball)

    context = {
        "work_dir": work_dir,
        "artifact_dir": artifact_dir,
        "scripts_dir": args.scripts_dir,
        "buildid": args.buildid,
        "dtbs_tarball": dtbs_tarball,
        "template_vars": args.template_vars,
        "qcom_ptool": unpacked_tarball(args.download_dir, "qcom-ptool"),
    }

    build_fit_images(
        boards,
        available_dtbs,
        dtbs_tarball,
        unpacked_tarball(args.download_dir, "qcom-dtb-metadata"),
        work_dir,
        artifact_dir,
    )

    generated = []
    for board in boards:
        name = board["name"]
        log(f"Board {name}: dtb_bin_type={board['dtb_bin_type']}")

        # skip if board dtb isn't present
        if board["dtb"] not in available_dtbs:
            log(f"Skipping board {name}: dtb {board['dtb']} not available")
            continue

        # skip if this is a multidtb board but its per-SoC FIT wasn't produced
        if (
            board["dtb_bin_type"] == "multidtb"
            and not (
                artifact_dir / f"dtb-multidtb-{board['soc_id']}.bin"
            ).exists()
        ):
            log(
                f"Skipping board {name}: multidtb FIT "
                f"dtb-multidtb-{board['soc_id']}.bin not available"
            )
            continue

        boot_binaries = board["boot_binaries"]
        archive = downloads / boot_binaries["filename"]
        download(
            boot_binaries["url"],
            archive,
            boot_binaries["sha256sum"],
            boot_binaries["description"],
        )
        unpack_boot_binaries(archive, work_dir / f"{name}_boot-binaries")

        cdt = board.get("cdt")
        if cdt:
            # unpack board CDT; some CDTs ship as .tar.gz, others as .zip
            cdt_archive = downloads / cdt["filename"]
            download(
                cdt["url"], cdt_archive, cdt["sha256sum"], cdt["description"]
            )
            unpack(cdt_archive, work_dir / f"{name}_cdt")

        for platform in board["ptool_platforms"]:
            generate_flash_dir(board, platform, context)
        generated.append(board)

    merge_spinor_dirs(generated, context)

    # cleanup
    shutil.rmtree(work_dir)


if __name__ == "__main__":
    main()
