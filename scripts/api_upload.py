"""Upload a CSV or JSON file through the Analytics API, using its pre-signed S3 form.

1. POST /uploads asks the API for a signed form (and a new dataset id).
2. The file is POSTed straight to S3 with that form. S3 enforces the limits the API signed.

Usage:
    python scripts/api_upload.py data/student_results.csv \\
        --api-url https://<id>.execute-api.ap-south-1.amazonaws.com/v1 --api-key <key>

The URL and key can also come from GRADEFLOW_API_URL and GRADEFLOW_API_KEY. Standard library only.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path


def build_multipart(fields: dict[str, str], filename: str, content: bytes) -> tuple[bytes, str]:
    """Encode a multipart/form-data body. S3 requires the `file` field to come LAST.

    Returns (body, content_type_header).
    """
    boundary = f"----gradeflow{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        header = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
        parts.append(f"{header}{value}\r\n".encode())
    file_header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    )
    parts.append(file_header.encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def request_upload_form(api_url: str, api_key: str, filename: str) -> dict:
    """Ask the API to sign an upload form for `filename`."""
    request = urllib.request.Request(
        f"{api_url.rstrip('/')}/uploads",
        data=json.dumps({"filename": filename}).encode(),
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def post_to_s3(form: dict, filename: str, content: bytes) -> int:
    """Send the file with the signed form. Returns S3's HTTP status (204 on success)."""
    body, content_type = build_multipart(form["fields"], filename, content)
    request = urllib.request.Request(
        form["url"], data=body, headers={"Content-Type": content_type}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file", type=Path)
    parser.add_argument("--api-url", default=os.environ.get("GRADEFLOW_API_URL"))
    parser.add_argument("--api-key", default=os.environ.get("GRADEFLOW_API_KEY"))
    args = parser.parse_args(argv)
    if not args.api_url or not args.api_key:
        parser.error(
            "give --api-url and --api-key (or set GRADEFLOW_API_URL and GRADEFLOW_API_KEY)"
        )

    content = args.file.read_bytes()
    try:
        upload = request_upload_form(args.api_url, args.api_key, args.file.name)
        print(f"dataset id : {upload['dataset_id']}")
        print(f"limit      : {upload['max_bytes']:,} bytes, form valid {upload['expires_in']} s")
        status = post_to_s3(upload["upload"], args.file.name, content)
    except urllib.error.HTTPError as error:  # the API (4xx) or S3 (for example EntityTooSmall)
        print(f"refused with HTTP {error.code}: {error.read().decode(errors='replace')[:500]}")
        return 1
    print(f"uploaded   : HTTP {status} ({len(content):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
