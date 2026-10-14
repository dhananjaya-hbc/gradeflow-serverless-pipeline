"""Unit tests for the file fingerprint."""

from gradeflow.domain.fingerprint import sha256_hex


def test_matches_known_sha256_vectors() -> None:
    assert sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert sha256_hex(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_same_content_gives_same_hash() -> None:
    assert sha256_hex(b"a,b\n1,2\n") == sha256_hex(b"a,b\n1,2\n")


def test_one_changed_byte_changes_the_hash() -> None:
    assert sha256_hex(b"a,b\n1,2\n") != sha256_hex(b"a,b\n1,3\n")


def test_hash_is_64_lowercase_hex_characters() -> None:
    digest = sha256_hex(b"anything")
    assert len(digest) == 64
    assert digest == digest.lower()
    int(digest, 16)  # raises if not hex
