#!/usr/bin/env python3
# Copyright (c) 2025 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from multiprocessing import Pool
import os
import sys
from time import sleep
from typing import List

import requests

gh_token = os.environ["GITHUB_TOKEN"]
num_threads_str = os.environ.get("UPLOAD_THREADS", "5")


def upload_file(args):
    """
    Uploads a file to our file upload service. The service is a GCP CloudRun
    project that returns signed URLs to Google Storage objects we can upload to.
    """
    try:
        url, base, name = args

        headers = {
            "Authentication": f"Bearer {gh_token}",
        }

        # Obtain the signed-url for GCS using Fibonacci backoff/retries
        for x in (1, 2, 3, 5, 0):
            r = requests.put(url, headers=headers, allow_redirects=False)
            if not r.ok:
                correlation_id = r.headers.get("X-Correlation-ID", "?")
                if not x:
                    return (
                        name,
                        f"Unable to get signed url HTTP_{r.status_code}. Correlation ID: {correlation_id} - {r.text}",
                    )
                else:
                    print(
                        f"Error getting signed URL for {name}: Correlation ID: {correlation_id} HTTP_{r.status_code} - {r.text}",
                        flush=True,
                    )
                    print(f"Retrying in {x} seconds", flush=True)
                    sleep(x)

        # Upload the file to the signed URL with backoff/retry logic
        url = r.headers["location"]
        path = os.path.join(base, name)
        for x in (1, 2, 3, 0):
            r = requests.put(
                url,
                data=open(path, "rb"),
                headers={"Content-type": "application/octet-stream"},
            )
            if not r.ok:
                if not x:
                    return (
                        name,
                        f"Unable to upload content HTTP_{r.status_code} - {r.text}",
                    )
                else:
                    print(
                        f"Unable to upload content for {name}: HTTP_{r.status_code} - {r.text}"
                    )
                    print(f"Retrying in {x} seconds")
                    sleep(x)

        return name, None
    except Exception as e:
        return name, str(e)


def get_files_to_publish(path: str) -> List[str]:
    paths = []
    for root, dirs, files in os.walk(path):
        for file in files:
            paths.append(os.path.join(root, file)[len(path) :])
    return paths


def main(num_threads: int, artifacts_dir: str, base_url: str):
    paths = get_files_to_publish(artifacts_dir)
    print(f"= Found {len(paths)} files to publish", flush=True)

    failed = False
    work = [(f"{base_url}{x}", artifacts_dir, x) for x in paths]
    with Pool(num_threads) as p:
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

    num_threads = int(num_threads_str)
    main(num_threads, BUILD_DIR, URL)
