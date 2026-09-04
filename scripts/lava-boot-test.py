#!/usr/bin/env python3
# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause
"""lava-boot-test.py - boot-test a qcom-deb-images build on a board in LAVA.

Takes a presigned URL to a "<prefix>-flash-ufs.tar.gz" or
"<prefix>-flash-emmc.tar.gz" bundle (as built by scripts/bundle-flash-dirs.sh
and published by the debos workflow) and submits a LAVA job that flashes it
onto a board and boots it.

The job definition is the same ci/lava/<board>/boot.yaml template that CI
submits, so a manual run exercises the same flash/boot path as a CI run --
including the per-board rawprogram/patch lists and multi-stage boots, which
differ enough between boards that they are not worth re-deriving here. Only
what must differ is rewritten:

  * the artifact URL becomes the presigned URL, and the QLIAuthorization
    header CI relies on is dropped, since a presigned URL authenticates
    itself (use --token-name to keep header-based auth instead);
  * visibility defaults to "personal": a presigned URL is a credential, and
    the job definition is readable by anyone who can see the job;
  * the job name and metadata lose their GitHub Actions placeholders.

Examples:

    # which boards have a template, and which bundle does each one need?
    scripts/lava-boot-test.py --list-boards

    # flash + boot + the template's smoke tests, and wait for the verdict
    scripts/lava-boot-test.py --board qcs6490-rb3gen2-vision-kit --wait \\
        'https://qcom-prd-gh-artifacts.s3.amazonaws.com/.../trixie-flash-ufs.tar.gz?X-Amz-...'

    # boot only, no tests; print the definition instead of submitting it
    scripts/lava-boot-test.py -b qrb2210-rb1 --boot-only --dry-run "$URL"

    # boot, then run a couple of commands as extra test cases
    scripts/lava-boot-test.py -b qcs615-ride --command 'uname -a' \\
        --command 'systemctl is-system-running' "$URL"

Authentication: a LAVA API token is read from $LAVATOKEN (the name CI uses),
$LAVA_TOKEN, or --token-file. Not needed for --dry-run.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_LAVA_HOST = "lava.infra.foundries.io"

# a LAVA instance is a bare hostname, optionally with a port; keeping
# --lava-host to that shape stops it from injecting a userinfo, path or
# query into the URL api_call() builds
HOST_RE = re.compile(r"\A[A-Za-z0-9.-]+(:[0-9]{1,5})?\Z")

# stands in for {{GITHUB_REPOSITORY}} in the job metadata
REPOSITORY = "qualcomm-linux/qcom-deb-images"

# ci/lava/<board>/boot.yaml, relative to this script
TEMPLATE_ROOT = Path(__file__).resolve().parent.parent / "ci" / "lava"
TEMPLATE_NAME = "boot.yaml"

# the bundle flavours scripts/bundle-flash-dirs.sh produces
FLAVOUR_RE = re.compile(r"-flash-(ufs|emmc)\.tar\.gz$")

# how often to poll, and how long to wait, in --wait mode
POLL_SECONDS = 30


# ---------------------------------------------------------------- templates


def list_templates():
    """Map board name -> (template path, bundle flavour, device type)."""
    boards = {}
    for template in sorted(TEMPLATE_ROOT.glob("*/" + TEMPLATE_NAME)):
        text = template.read_text()
        boards[template.parent.name] = (
            template,
            template_flavour(text, template),
            template_device_type(text),
        )
    return boards


def template_url_line(lines, template):
    """Index of the template's artifact URL line.

    The URL is the one line every template shares that has to be rewritten, so
    finding exactly one is also a sanity check that the template still looks
    the way this script expects.
    """
    hits = [
        i
        for i, line in enumerate(lines)
        if line.strip().startswith("url:") and "{{BUILD_DOWNLOAD_URL}}" in line
    ]
    if len(hits) != 1:
        sys.exit(
            f"error: {template}: expected exactly one 'url:' line containing "
            f"{{{{BUILD_DOWNLOAD_URL}}}}, found {len(hits)}. The template "
            f"layout changed; update this script."
        )
    return hits[0]


def template_flavour(text, template):
    """Which bundle flavour ('ufs'/'emmc') a template expects to be given."""
    lines = text.splitlines()
    line = lines[template_url_line(lines, template)]
    match = FLAVOUR_RE.search(line.strip().rstrip("\"'"))
    if not match:
        sys.exit(
            f"error: {template}: could not tell from its url line whether it "
            f"wants a -flash-ufs or -flash-emmc bundle: {line.strip()}"
        )
    return match.group(1)


def template_device_type(text):
    for line in text.splitlines():
        if line.startswith("device_type:"):
            return line.split(":", 1)[1].strip()
    return "?"


# ------------------------------------------------- indentation-aware editing
#
# The templates are edited as text rather than parsed as YAML: it keeps this
# script dependency-free (no PyYAML), mirrors what ci/lava-test.yml already
# does with sed, and makes --dry-run output diffable against the template.
# Every edit asserts its anchor exists, so a template reshuffle fails loudly
# here instead of producing a subtly wrong job.


def indent_of(line):
    return len(line) - len(line.lstrip())


def mapping_block_end(lines, start):
    """End of the block introduced by the mapping key at `lines[start]`.

    The block is the following lines that are more deeply indented, plus any
    sequence items at the key's own indentation -- top-level YAML lists are
    written flush with their key in these templates, as in a "tags:" key
    followed by "- cambridge-lab".
    """
    key_indent = indent_of(lines[start])
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if not line.strip():
            end += 1
            continue
        if indent_of(line) > key_indent or (
            indent_of(line) == key_indent and line.lstrip().startswith("- ")
        ):
            end += 1
            continue
        break
    return end


def item_block_end(lines, start):
    """End of the sequence item at `lines[start]`: its indented body only.

    Unlike mapping_block_end() this stops at the next sibling item, so it can
    excise one action out of the actions list.
    """
    item_indent = indent_of(lines[start])
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if not line.strip():
            end += 1
            continue
        if indent_of(line) > item_indent:
            end += 1
            continue
        break
    return end


def find_line(lines, predicate):
    for i, line in enumerate(lines):
        if predicate(line):
            return i
    return None


def yaml_quote(value):
    """Quote a string for YAML.

    JSON strings are valid YAML double-quoted scalars, so json.dumps() gives
    correct escaping for the '&', '=', '%' and '/' that presigned URLs are full
    of, as well as for any embedded quote.
    """
    return json.dumps(value)


def replace_scalar(lines, index, key, value):
    """Rewrite `lines[index]` as '<indent><key>: <quoted value>'."""
    indent = " " * indent_of(lines[index])
    lines[index] = f"{indent}{key}: {yaml_quote(value)}"


def set_top_level(lines, key, value):
    """Set a top-level scalar key, appending it if the template lacks it."""
    index = find_line(lines, lambda ln: ln.startswith(f"{key}:"))
    if index is None:
        lines.append(f"{key}: {yaml_quote(value)}")
    else:
        replace_scalar(lines, index, key, value)


def drop_block(lines, index, mapping=True):
    block_end = mapping_block_end if mapping else item_block_end
    del lines[index:block_end(lines, index)]


def actions_end(lines):
    """Index just past the last item of the top-level 'actions:' list."""
    start = find_line(lines, lambda ln: ln.startswith("actions:"))
    if start is None:
        sys.exit("error: template has no top-level 'actions:' key")
    return mapping_block_end(lines, start)


# ------------------------------------------------------------ job definition


def inline_test_action(commands, timeout_minutes):
    """An inline lava-test-shell action running `commands` as test cases.

    Each command becomes its own test case, so a LAVA result table shows which
    one failed rather than a single pass/fail for the lot.
    """
    out = [
        "- test:",
        "    timeout:",
        f"      minutes: {timeout_minutes}",
        "    definitions:",
        "    - from: inline",
        "      name: manual-commands",
        "      path: inline/manual-commands.yaml",
        "      repository:",
        "        metadata:",
        "          format: Lava-Test Test Definition 1.0",
        "          name: manual-commands",
        "          description: commands from lava-boot-test.py --command",
        "        run:",
        "          steps:",
    ]
    for n, command in enumerate(commands, start=1):
        step = f"lava-test-case cmd{n} --shell {command}"
        out.append(f"          - {yaml_quote(step)}")
    return out


def build_definition(args, template):
    """Render a submittable job definition from a ci/lava template."""
    lines = template.read_text().splitlines()

    # 1) point the deploy at the presigned URL
    url_index = template_url_line(lines, template)
    url_indent = indent_of(lines[url_index])
    replace_scalar(lines, url_index, "url", args.url)

    # 2) auth: a presigned URL carries its own credentials, so CI's
    #    QLIAuthorization header is not just unnecessary but actively wrong
    #    (the token name only resolves for a user who owns it). The header
    #    block sits inside the same qcomflash mapping as the url.
    header_index = find_line(
        lines,
        lambda ln: ln.strip() == "headers:" and indent_of(ln) == url_indent,
    )
    if args.token_name:
        if header_index is None:
            sys.exit(
                "error: --token-name given but the template has no headers: "
                "block to rewrite"
            )
        drop_block(lines, header_index)
        lines[header_index:header_index] = [
            " " * url_indent + "headers:",
            " " * (url_indent + 2) + f"QLIAuthorization: {args.token_name}",
        ]
    elif header_index is not None:
        drop_block(lines, header_index)

    # 3) drop the template's test actions for a pure boot test
    if args.boot_only:
        while True:
            index = find_line(lines, lambda ln: ln.rstrip() == "- test:")
            if index is None:
                break
            drop_block(lines, index, mapping=False)

    # 4) append ad-hoc commands as an extra inline test action
    if args.command:
        insert_at = actions_end(lines)
        lines[insert_at:insert_at] = inline_test_action(
            args.command, args.command_timeout
        )

    # 5) tags: keep the template's by default -- they can be what pins a job to
    #    the lab that actually has the board wired up.
    tags_index = find_line(lines, lambda ln: ln.startswith("tags:"))
    tags = [] if args.no_tags else template_tags(lines, tags_index)
    tags += [t for t in args.tag if t not in tags]
    if tags_index is not None:
        drop_block(lines, tags_index)
    if tags:
        lines += ["tags:"] + [f"- {t}" for t in tags]

    # 6) identify the job, and replace the placeholders CI would have filled
    set_top_level(lines, "job_name", args.job_name)
    set_top_level(lines, "visibility", args.visibility)
    if args.priority is not None:
        set_top_level(lines, "priority", args.priority)

    text = "\n".join(lines) + "\n"
    text = text.replace("{{GITHUB_SHA}}", local_commit())
    text = text.replace("{{GITHUB_REPOSITORY}}", REPOSITORY)
    text = text.replace("{{GITHUB_RUN_ID}}", "manual")
    text = text.replace("{{GITHUB_RUN_ATTEMPT}}", "0")
    text = text.replace("{{SUITE}}", args.suite or "manual")

    leftover = sorted(set(re.findall(r"{{[A-Z_]+}}", text)))
    if leftover:
        sys.exit(
            f"error: {template}: unsubstituted placeholder(s) "
            f"{' '.join(leftover)}; update this script"
        )
    return text


def template_tags(lines, tags_index):
    if tags_index is None:
        return []
    end = mapping_block_end(lines, tags_index)
    return [
        line.lstrip()[2:].strip()
        for line in lines[tags_index + 1:end]
        if line.lstrip().startswith("- ")
    ]


def local_commit():
    """Short HEAD of this checkout, for the job's build-commit metadata."""
    try:
        out = subprocess.run(
            ["git", "-C", str(TEMPLATE_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


# -------------------------------------------------------------- the LAVA API


def api_call(host, path, token, payload=None, method=None):
    """Call the LAVA REST API v0.2; return (status, decoded body)."""
    if not HOST_RE.match(host):
        sys.exit(f"error: not a valid LAVA host: {host}")
    url = f"https://{host}/api/v0.2/{path}"
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = urllib.parse.urlencode(payload).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = f"Token {token}"
    request = urllib.request.Request(
        url, data=data, headers=headers, method=method
    )
    # the scheme is a literal above and the host is validated, so this can
    # only ever be an https request, never a local file; belt and braces in
    # case the URL grows more moving parts later
    if request.type != "https":
        sys.exit(f"error: refusing to call non-https URL: {url}")
    try:
        # nosemgrep
        with urllib.request.urlopen(request) as response:
            body = response.read().decode(errors="replace")
            status = response.status
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors="replace")
        status = error.code
    except urllib.error.URLError as error:
        sys.exit(f"error: cannot reach https://{host}: {error.reason}")
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body


def read_token(args):
    if args.token_file:
        try:
            return Path(args.token_file).expanduser().read_text().strip()
        except OSError as error:
            sys.exit(f"error: cannot read --token-file: {error}")
    for name in ("LAVATOKEN", "LAVA_TOKEN"):
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    sys.exit(
        "error: no LAVA API token. Set $LAVATOKEN (or $LAVA_TOKEN), or pass\n"
        "       --token-file PATH. Create one under 'API tokens' in your\n"
        "       LAVA profile. Use --dry-run to render without a token."
    )


def check_auth(status, body):
    """Turn an authentication/permission failure into a clear message.

    Without this a rejected token surfaces as whatever the caller was doing
    ("definition is INVALID"), which sends you looking in the wrong place.
    """
    if status in (401, 403):
        sys.exit(
            f"error: LAVA rejected the API token (HTTP {status}): {body}\n"
            f"       Check $LAVATOKEN / --token-file against the token in\n"
            f"       your LAVA profile under 'API tokens'."
        )


def submit(host, token, definition):
    status, body = api_call(
        host, "jobs/", token, payload={"definition": definition}
    )
    check_auth(status, body)
    if status not in (200, 201):
        sys.exit(f"error: submission failed (HTTP {status}): {body}")
    ids = []
    if isinstance(body, dict):
        ids = body.get("job_ids") or [
            body[key] for key in ("job_id", "id") if key in body
        ]
    elif isinstance(body, list):
        ids = body
    if not ids:
        sys.exit(f"error: submitted but no job id in the reply: {body}")
    return [str(i) for i in ids]


def validate(host, token, definition):
    status, body = api_call(
        host, "jobs/validate/", token, payload={"definition": definition}
    )
    check_auth(status, body)
    if status == 404:
        print(
            "note: this LAVA has no jobs/validate/ endpoint; falling back to "
            "--dry-run output only",
            file=sys.stderr,
        )
        return True
    if status in (200, 201):
        print(f"definition is valid ({body if body else 'no warnings'})")
        return True
    print(f"definition is INVALID (HTTP {status}): {body}", file=sys.stderr)
    return False


def wait_for(host, token, job_id, timeout_minutes):
    """Poll a job to completion; return True if it passed."""
    deadline = time.monotonic() + timeout_minutes * 60
    last = None
    while time.monotonic() < deadline:
        status, body = api_call(host, f"jobs/{job_id}/", token)
        if status != 200 or not isinstance(body, dict):
            print(
                f"warning: cannot read job state (HTTP {status})",
                file=sys.stderr,
            )
            time.sleep(POLL_SECONDS)
            continue
        state, health = body.get("state"), body.get("health")
        if (state, health) != last:
            device = body.get("actual_device") or "-"
            print(
                f"  {state:<12} health={health:<12} device={device}",
                flush=True,
            )
            last = (state, health)
        if state == "Finished":
            return report(host, token, job_id, health)
        time.sleep(POLL_SECONDS)
    print(
        f"warning: still not finished after {timeout_minutes} min; "
        f"leaving it running",
        file=sys.stderr,
    )
    return False


def report(host, token, job_id, health):
    """Print the per-test-case results of a finished job."""
    status, body = api_call(host, f"jobs/{job_id}/tests/", token)
    cases = body.get("results", body) if isinstance(body, dict) else body
    if status == 200 and isinstance(cases, list) and cases:
        print("\nresults:")
        for case in cases:
            if not isinstance(case, dict):
                continue
            name = case.get("name", "?")
            suite = case.get("suite", "?")
            result = str(case.get("result", "?"))
            marks = {"pass": "PASS", "fail": "FAIL", "skip": "skip"}
            print(f"  [{marks.get(result, result):<4}] {suite}: {name}")
    print(f"\njob health: {health}")
    return health == "Complete"


# -------------------------------------------------------------------- driver


def parse_bundle(url):
    """(flavour, prefix) from a bundle URL, ignoring the presign query."""
    path = urllib.parse.unquote(urllib.parse.urlsplit(url).path)
    name = Path(path).name
    match = FLAVOUR_RE.search(name)
    if not match:
        return None, None
    return match.group(1), name[: match.start()]


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "url",
        nargs="?",
        help="presigned URL of a <prefix>-flash-{ufs,emmc}.tar.gz bundle",
    )
    parser.add_argument(
        "-b",
        "--board",
        help="board to test: a directory under ci/lava/ (see --list-boards)",
    )
    parser.add_argument(
        "--list-boards",
        action="store_true",
        help="list the boards with a template, and exit",
    )
    parser.add_argument(
        "--boot-only",
        action="store_true",
        help="drop the template's smoke tests: flash and boot only",
    )
    parser.add_argument(
        "--command",
        action="append",
        default=[],
        metavar="CMD",
        help="run CMD on the booted board as a test case (repeatable)",
    )
    parser.add_argument(
        "--command-timeout",
        type=int,
        default=10,
        metavar="MIN",
        help="timeout for the --command test action (default: 10 minutes)",
    )
    parser.add_argument(
        "--job-name", help="LAVA job name (default: auto-generated)"
    )
    parser.add_argument(
        "--visibility",
        default="personal",
        choices=("personal", "public"),
        help="job visibility. A presigned URL is a credential and it is "
        "stored "
        "in the job definition, so this defaults to 'personal'",
    )
    parser.add_argument(
        "--priority",
        help="job priority: 0-100, or low/medium/high (default: template's)",
    )
    parser.add_argument(
        "--tag",
        action="append",
        default=[],
        help="add a device tag (repeatable); the template's tags are kept",
    )
    parser.add_argument(
        "--no-tags",
        action="store_true",
        help="drop the template's device tags. These can be what pins the job "
        "to the lab holding the board, so expect it to queue forever",
    )
    parser.add_argument(
        "--token-name",
        metavar="NAME",
        help="keep header auth, using this LAVA remote-artifact token name "
        "instead of a presigned URL (as CI does)",
    )
    parser.add_argument(
        "--suite",
        help="suite label for the job name (default: from the bundle name)",
    )
    parser.add_argument(
        "--lava-host",
        default=os.environ.get("LAVA_HOST", DEFAULT_LAVA_HOST),
        help=f"LAVA instance (default: {DEFAULT_LAVA_HOST})",
    )
    parser.add_argument("--token-file", help="file holding the LAVA API token")
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="print the job definition instead of submitting it",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="have LAVA validate the definition, but do not submit it",
    )
    parser.add_argument(
        "-w",
        "--wait",
        action="store_true",
        help="wait for the job to finish and print its results",
    )
    parser.add_argument(
        "--wait-timeout",
        type=int,
        default=240,
        metavar="MIN",
        help="how long to wait in --wait mode (default: 240 minutes)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="submit even if the bundle flavour does not match the board",
    )
    args = parser.parse_args()

    boards = list_templates()
    if not boards:
        sys.exit(
            f"error: no <board>/{TEMPLATE_NAME} templates under "
            f"{TEMPLATE_ROOT}"
        )

    if args.list_boards:
        width = max(len(b) for b in boards)
        print(f"{'board'.ljust(width)}  bundle  device type")
        for board, (_, flavour, device_type) in boards.items():
            print(f"{board.ljust(width)}  {flavour:<6}  {device_type}")
        return 0

    if not args.url or not args.board:
        parser.error("both a URL and --board are required (see --list-boards)")
    if args.board not in boards:
        sys.exit(
            f"error: no template for board {args.board!r}. "
            f"Known boards: {', '.join(boards)}"
        )
    template, board_flavour, device_type = boards[args.board]

    scheme = urllib.parse.urlsplit(args.url).scheme
    if scheme != "https":
        sys.exit(f"error: URL must be https, got {scheme or 'no'} scheme")

    url_flavour, prefix = parse_bundle(args.url)
    if url_flavour is None:
        print(
            "warning: URL does not name a -flash-ufs.tar.gz or "
            "-flash-emmc.tar.gz bundle; cannot check it suits this board",
            file=sys.stderr,
        )
    elif url_flavour != board_flavour:
        message = (
            f"{args.board} flashes from the {board_flavour} bundle, but the "
            f"URL points at a {url_flavour} one"
        )
        if not args.force:
            sys.exit(f"error: {message}. Pass --force to submit anyway.")
        print(
            f"warning: {message}; continuing because of --force",
            file=sys.stderr,
        )

    if not args.token_name and not urllib.parse.urlsplit(args.url).query:
        print(
            "warning: URL has no query string, so it looks unsigned; LAVA "
            "will fetch it anonymously and the deploy will fail if it is "
            "private (use --token-name to authenticate with a LAVA token)",
            file=sys.stderr,
        )

    args.suite = args.suite or prefix
    if not args.job_name:
        bits = ["boot test", args.board]
        if args.suite:
            bits.append(args.suite)
        if args.boot_only and not args.command:
            bits.append("(boot only)")
        args.job_name = " ".join(bits)

    definition = build_definition(args, template)

    if args.dry_run:
        print(definition, end="")
        return 0

    token = read_token(args)
    if args.validate:
        return 0 if validate(args.lava_host, token, definition) else 1

    if args.visibility == "public":
        print(
            "warning: --visibility public puts the presigned URL in a job "
            "definition that anyone on this LAVA can read",
            file=sys.stderr,
        )

    job_ids = submit(args.lava_host, token, definition)
    for job_id in job_ids:
        url = f"https://{args.lava_host}/scheduler/job/{job_id}"
        print(f"submitted job {job_id} ({device_type}): {url}")

    if not args.wait:
        return 0
    ok = True
    for job_id in job_ids:
        print(f"\nwaiting for job {job_id} ...")
        ok = wait_for(args.lava_host, token, job_id, args.wait_timeout) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
