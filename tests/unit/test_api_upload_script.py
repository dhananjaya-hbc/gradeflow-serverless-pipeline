"""Unit tests for scripts/api_upload.py: the multipart body S3 expects."""

import re

import pytest
from scripts import api_upload
from scripts.api_upload import build_multipart

FIELDS = {"key": "uploads/ds-1/a.csv", "policy": "abc==", "x-amz-signature": "def"}


def split(body: bytes, content_type: str) -> list[bytes]:
    boundary = content_type.split("boundary=")[1].encode()
    return [part for part in body.split(b"--" + boundary) if part.strip(b"\r\n-")]


def test_the_content_type_names_the_boundary_used_in_the_body() -> None:
    body, content_type = build_multipart(FIELDS, "a.csv", b"x")
    boundary = content_type.split("boundary=")[1]
    assert content_type.startswith("multipart/form-data; boundary=")
    assert body.startswith(f"--{boundary}\r\n".encode())
    assert body.endswith(f"--{boundary}--\r\n".encode())


def test_every_signed_field_is_sent_in_order_and_the_file_comes_last() -> None:
    body, content_type = build_multipart(FIELDS, "a.csv", b"student_id,score\n")

    parts = split(body, content_type)
    names = [re.search(rb'name="([^"]+)"', part).group(1).decode() for part in parts]

    assert names == ["key", "policy", "x-amz-signature", "file"]  # S3 ignores fields after `file`
    assert b'filename="a.csv"' in parts[-1]
    assert parts[0].split(b"\r\n\r\n")[1].strip() == b"uploads/ds-1/a.csv"


@pytest.mark.parametrize(
    "content", [b"", b"plain", b"line1\r\nline2\r\n", bytes(range(256)), b"--boundary-like"]
)
def test_the_file_bytes_arrive_unchanged(content: str) -> None:
    body, content_type = build_multipart(FIELDS, "a.csv", content)
    file_part = split(body, content_type)[-1]
    assert file_part.split(b"\r\n\r\n", 1)[1].removesuffix(b"\r\n") == content


def test_each_call_uses_a_fresh_boundary() -> None:
    first = build_multipart(FIELDS, "a.csv", b"x")[1]
    assert first != build_multipart(FIELDS, "a.csv", b"x")[1]


def test_the_command_line_needs_the_url_and_the_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.delenv("GRADEFLOW_API_URL", raising=False)
    monkeypatch.delenv("GRADEFLOW_API_KEY", raising=False)
    file = tmp_path / "a.csv"
    file.write_bytes(b"x")
    with pytest.raises(SystemExit) as error:
        api_upload.main([str(file)])
    assert error.value.code == 2  # argparse's "you used it wrong"


def test_the_api_url_and_key_can_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("GRADEFLOW_API_URL", "https://api.example.com/v1/")
    monkeypatch.setenv("GRADEFLOW_API_KEY", "secret")
    sent: dict = {}

    def fake_request(url: str, key: str, filename: str) -> dict:
        sent.update(url=url, key=key, filename=filename)
        return {"dataset_id": "ds-1", "max_bytes": 100, "expires_in": 900,
                "upload": {"url": "https://s3.example.com/", "fields": FIELDS}}  # fmt: skip

    monkeypatch.setattr(api_upload, "request_upload_form", fake_request)
    monkeypatch.setattr(api_upload, "post_to_s3", lambda form, name, content: 204)
    file = tmp_path / "results.csv"
    file.write_bytes(b"abc")

    assert api_upload.main([str(file)]) == 0
    assert sent == {
        "url": "https://api.example.com/v1/",
        "key": "secret",
        "filename": "results.csv",
    }
