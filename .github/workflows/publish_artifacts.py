#!/usr/bin/env python3
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

from multiprocessing import Pool
import os
import sys
from typing import List

import requests

gh_token = os.environ["GITHUB_TOKEN"]


def upload_file(args):
    try:
        url, base, name = args

        headers = {
            "Authentication": f"Bearer {gh_token}",
        }
        r = requests.put(url, headers=headers, allow_redirects=False)
        if not r.ok:
            return name, f"Unable to get signed url HTTP_{r.status_code} - {r.text}"

        url = r.headers["location"]
        path = os.path.join(base, name)
        r = requests.put(
            url, data=open(path, "rb"), headers={"Content-type": "application/octet-stream"}
        )
        if not r.ok:
            return name, f"Unable to upload content HTTP_{r.status_code} - {r.text}"

        return name, None
    except Exception as e:
        return name, str(e)


def get_files_to_publish(path: str) -> List[str]:
    paths = []
    for root, dirs, files in os.walk(path):
        for file in files:
            paths.append(os.path.join(root, file)[len(path) :])
    return paths


def main(artifacts_dir: str, base_url: str):
    paths = get_files_to_publish(artifacts_dir)
    print(f"= Found {len(paths)} files to publish", flush=True)

    failed = False
    work = [(f"{base_url}{x}", artifacts_dir, x) for x in paths]
    with Pool(5) as p:
        results = p.imap_unordered(upload_file, work)
        for i, res in enumerate(results):
            name, err = res
            print(f"= {i+1} of {len(work)} - {name}", flush=True)
            if err:
                print(f"|-> ERROR: {err}", flush=True)
                failed = True

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    BUILD_DIR = os.environ["BUILD_DIR"]
    if BUILD_DIR[-1] != "/":
        BUILD_DIR = BUILD_DIR + "/"

    URL = os.environ["URL"]
    if URL[-1] != "/":
        URL = URL + "/"

    main(BUILD_DIR, URL)
