"""File fingerprint used to detect duplicate uploads."""

import hashlib


def sha256_hex(content: bytes) -> str:
    """Return the SHA-256 of the file bytes as 64 lower-case hex characters."""
    return hashlib.sha256(content).hexdigest()
