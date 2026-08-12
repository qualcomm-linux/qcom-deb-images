"""Unit tests for scripts/gen-flash-dirs.py.

These exercise the pure parts of the script -- board validation, selection
and the file filters -- so a bad boards.yaml or a regression in board
selection is caught without running a whole debos build.

Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
SPDX-License-Identifier: BSD-3-Clause
"""

import copy
import importlib.util
import tarfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "gen-flash-dirs.py"
BOARDS_YAML = REPO_ROOT / "boards.yaml"


def _load_script():
    """Import gen-flash-dirs.py, whose name is not a valid module name."""
    spec = importlib.util.spec_from_file_location("gen_flash_dirs", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gfd = _load_script()

# a board with every required field, valid and as small as possible; tests
# deep-copy this and break one thing at a time
VALID_BOARD = {
    "name": "test-board",
    "soc_id": "testsoc",
    "dtb": "qcom/test-board.dtb",
    "ptool_platforms": ["test-board/ufs"],
    "boot_binaries": {
        "description": "Test boot binaries",
        "url": "https://example.com/boot-binaries.zip",
        "filename": "test_boot-binaries.zip",
        "sha256sum": "a" * 64,
    },
}


def board(**overrides):
    """A copy of VALID_BOARD with overrides applied; None deletes a key."""
    result = copy.deepcopy(VALID_BOARD)
    for key, value in overrides.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = value
    return result


def validation_errors(boards):
    """Run validate_boards() and return the message it died with."""
    with pytest.raises(SystemExit) as excinfo:
        gfd.validate_boards(boards)
    return str(excinfo.value)


class TestShippedBoardsYaml:
    """The board definitions this repository actually ships."""

    def test_boards_yaml_is_valid(self):
        """boards.yaml must satisfy its own schema."""
        assert gfd.load_boards(BOARDS_YAML)

    def test_lava_boards_are_defined(self):
        """Every board LAVA boots needs a flash directory built for it.

        ci/lava/<board>/boot.yaml unpacks flash_<board>_<storage>/ out of the
        flash tarball, so a LAVA board missing from boards.yaml can never be
        tested.
        """
        defined = {b["name"] for b in gfd.load_boards(BOARDS_YAML)}
        lava = {p.name for p in (REPO_ROOT / "ci" / "lava").iterdir()
                if p.is_dir()}
        assert lava <= defined, (
            f"LAVA boards not in boards.yaml: {sorted(lava - defined)}"
        )


class TestValidateBoards:
    """Schema validation of the board definitions."""

    def test_minimal_board_is_valid(self):
        gfd.validate_boards([board()])

    def test_boards_must_not_be_empty(self):
        assert "non-empty list" in validation_errors([])

    def test_board_must_be_a_mapping(self):
        assert "expected a mapping" in validation_errors(["test-board"])

    @pytest.mark.parametrize("key", gfd.BOARD_REQUIRED)
    def test_missing_required_key(self, key):
        errors = validation_errors([board(**{key: None})])
        assert f"missing required key '{key}'" in errors

    def test_unknown_key_is_rejected(self):
        """A typo must fail the build rather than be silently ignored."""
        errors = validation_errors([board(sha256sums="oops")])
        assert "unknown key 'sha256sums'" in errors

    def test_duplicate_board_name(self):
        errors = validation_errors([board(), board()])
        assert "duplicate board name" in errors

    def test_empty_name(self):
        errors = validation_errors([board(name="")])
        assert "'name' must be a non-empty string" in errors

    def test_dtb_must_be_a_dtb(self):
        errors = validation_errors([board(dtb="qcom/test-board.dts")])
        assert "'dtb' must name a .dtb file" in errors

    def test_bad_dtb_bin_type(self):
        errors = validation_errors([board(dtb_bin_type="fitimage")])
        assert "'dtb_bin_type' must be one of" in errors

    @pytest.mark.parametrize("value", gfd.DTB_BIN_TYPES)
    def test_good_dtb_bin_type(self, value):
        gfd.validate_boards([board(dtb_bin_type=value)])

    @pytest.mark.parametrize(
        "platforms",
        [[], "test-board/ufs", ["test-board"], ["/ufs"], [""]],
        ids=["empty", "not-a-list", "no-storage", "no-platform", "blank"],
    )
    def test_bad_ptool_platforms(self, platforms):
        assert validation_errors([board(ptool_platforms=platforms)])

    def test_multiple_errors_are_reported_together(self):
        """One run should surface every problem, not just the first."""
        errors = validation_errors([board(soc_id="", dtb="bad")])
        assert "'soc_id' must be a non-empty string" in errors
        assert "'dtb' must name a .dtb file" in errors

    def test_error_names_the_board(self):
        errors = validation_errors([board(soc_id="")])
        assert "board 'test-board'" in errors

    def test_error_without_a_name_gives_the_position(self):
        errors = validation_errors([board(), board(name=None)])
        assert "board #2" in errors


class TestValidateDownloads:
    """Validation of the boot_binaries and cdt archive definitions."""

    def test_non_https_url(self):
        spec = board()
        spec["boot_binaries"]["url"] = "http://example.com/boot.zip"
        assert "must be an https:// URL" in validation_errors([spec])

    @pytest.mark.parametrize(
        "sha256sum", ["A" * 64, "a" * 63, "a" * 65, "z" * 64],
        ids=["uppercase", "too-short", "too-long", "not-hex"],
    )
    def test_bad_sha256sum(self, sha256sum):
        spec = board()
        spec["boot_binaries"]["sha256sum"] = sha256sum
        assert "64 lowercase hex digits" in validation_errors([spec])

    def test_filename_must_be_a_single_component(self):
        """filename is joined onto the download directory."""
        spec = board()
        spec["boot_binaries"]["filename"] = "../boot.zip"
        assert "must not contain '/'" in validation_errors([spec])

    def test_unknown_key_in_boot_binaries(self):
        spec = board()
        spec["boot_binaries"]["file"] = "boot.mbn"
        assert "unknown key 'file'" in validation_errors([spec])

    def test_cdt_requires_file(self):
        """The CDT archive names the file to extract from it."""
        cdt = {
            "description": "Test CDT",
            "url": "https://example.com/cdt.zip",
            "filename": "test_cdt.zip",
            "sha256sum": "b" * 64,
        }
        errors = validation_errors([board(cdt=cdt)])
        assert "missing required key 'file'" in errors
        cdt["file"] = "cdt_test.bin"
        gfd.validate_boards([board(cdt=cdt)])


class TestLoadBoards:
    """Parsing and defaulting on top of validation."""

    def write(self, tmp_path, document):
        path = tmp_path / "boards.yaml"
        path.write_text(yaml.safe_dump(document))
        return path

    def test_dtb_bin_type_defaults_to_combineddtb(self, tmp_path):
        path = self.write(tmp_path, {"boards": [board()]})
        assert gfd.load_boards(path)[0]["dtb_bin_type"] == "combineddtb"

    def test_dtb_bin_type_is_preserved(self, tmp_path):
        path = self.write(
            tmp_path, {"boards": [board(dtb_bin_type="multidtb")]}
        )
        assert gfd.load_boards(path)[0]["dtb_bin_type"] == "multidtb"

    def test_missing_boards_key(self, tmp_path):
        path = self.write(tmp_path, {"board": [board()]})
        with pytest.raises(SystemExit, match="no 'boards' key"):
            gfd.load_boards(path)


class TestSelectBoards:
    """Filtering the board list down to --target-boards."""

    @pytest.fixture
    def boards(self):
        return [
            board(name="board-a"),
            board(name="board-b"),
            # only buildable when -t u_boot_test:<path> is passed
            board(name="board-uboot", u_boot_file_var="u_boot_test"),
        ]

    def test_all_skips_boards_missing_their_template_var(self, boards):
        selected = gfd.select_boards(boards, "all", {})
        assert [b["name"] for b in selected] == ["board-a", "board-b"]

    def test_all_includes_them_once_the_var_is_set(self, boards):
        selected = gfd.select_boards(
            boards, "all", {"u_boot_test": "boot.img"}
        )
        assert len(selected) == 3

    def test_subset(self, boards):
        selected = gfd.select_boards(boards, "board-b", {})
        assert [b["name"] for b in selected] == ["board-b"]

    def test_selection_keeps_yaml_order(self, boards):
        """Order comes from boards.yaml, not from --target-boards."""
        selected = gfd.select_boards(boards, "board-b,board-a", {})
        assert [b["name"] for b in selected] == ["board-a", "board-b"]

    @pytest.mark.parametrize(
        "target",
        ["board-a, board-b", " board-a,board-b ", "board-a,,board-b"],
        ids=["spaces", "surrounding-space", "empty-entry"],
    )
    def test_whitespace_and_empty_entries(self, boards, target):
        selected = gfd.select_boards(boards, target, {})
        assert [b["name"] for b in selected] == ["board-a", "board-b"]

    def test_unknown_board_lists_the_available_ones(self, boards):
        with pytest.raises(SystemExit) as excinfo:
            gfd.select_boards(boards, "board-z", {})
        message = str(excinfo.value)
        assert "board-z is not a known board" in message
        assert "board-a" in message

    def test_board_missing_its_template_var_says_why(self, boards):
        """Naming a real but unbuildable board must not read as a typo."""
        with pytest.raises(SystemExit) as excinfo:
            gfd.select_boards(boards, "board-uboot", {})
        message = str(excinfo.value)
        assert "requires -t u_boot_test:<path>" in message
        assert "not a known board" not in message


class TestBoardUBootFile:
    def test_none_when_the_board_needs_no_u_boot(self):
        assert gfd.board_u_boot_file(board(), {}) is None

    def test_resolves_the_template_var(self):
        spec = board(u_boot_file_var="u_boot_test")
        assert gfd.board_u_boot_file(spec, {"u_boot_test": "rb1.img"}) == (
            "rb1.img"
        )

    def test_none_when_the_var_is_unset(self):
        spec = board(u_boot_file_var="u_boot_test")
        assert gfd.board_u_boot_file(spec, {}) is None


class TestDtbsInTarball:
    def test_missing_tarball_is_empty(self, tmp_path):
        assert gfd.dtbs_in_tarball(tmp_path / "absent.tar.gz") == set()

    def test_only_dtbs_are_listed(self, tmp_path):
        member = tmp_path / "member"
        member.write_text("")
        tarball = tmp_path / "dtbs.tar.gz"
        with tarfile.open(tarball, "w:gz") as tar:
            for name in ("qcom/a.dtb", "qcom/b.dtb", "qcom/c.dtbo", "README"):
                tar.add(member, arcname=name)
        assert gfd.dtbs_in_tarball(tarball) == {"qcom/a.dtb", "qcom/b.dtb"}


class TestCopyBootBinaries:
    """The include/exclude filters applied to unpacked boot binaries."""

    def test_filters(self, tmp_path):
        source = tmp_path / "boot-binaries"
        (source / "sub").mkdir(parents=True)
        wanted = ["xbl.elf", "abl.mbn", "cdt.bin", "prog_firehose.elf",
                  "LICENSE", "sub/tz.melf"]
        unwanted = ["gpt_main0.bin", "rawprogram0.xml", "patch0.xml",
                    "wipe_rawprogram0.xml", "zeros_1.bin", "notes.txt"]
        for name in wanted + unwanted:
            (source / name).write_text(name)

        flash_dir = tmp_path / "flash"
        flash_dir.mkdir()
        gfd.copy_boot_binaries(source, flash_dir)

        copied = {p.name for p in flash_dir.iterdir()}
        assert copied == {Path(name).name for name in wanted}

    def test_partition_files_never_clobber_ptool_output(self, tmp_path):
        """Vendor rawprogram/gpt files must not overwrite ptool's."""
        source = tmp_path / "boot-binaries"
        source.mkdir()
        (source / "rawprogram0.xml").write_text("vendor")

        flash_dir = tmp_path / "flash"
        flash_dir.mkdir()
        (flash_dir / "rawprogram0.xml").write_text("ptool")
        gfd.copy_boot_binaries(source, flash_dir)

        assert (flash_dir / "rawprogram0.xml").read_text() == "ptool"
