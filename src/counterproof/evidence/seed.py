"""Per-run secret seed (FR-MU-05, PRD sections 10 and 15.3).

``seed = HMAC(secret, run_id)`` where ``secret`` is a per-user random key kept in the user
cache directory. Only ``sha256(seed)`` (the seed commitment) goes into the manifest, so the
mutant selection cannot be predicted from anything inside the repository.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path
from typing import Final

from counterproof.errors import UsageError

SECRET_FILENAME: Final = "secret.key"  # noqa: S105 - a file name, not a secret
_SECRET_BYTES: Final = 32
_MIN_SEED_BYTES: Final = 8


def load_or_create_secret(path: Path) -> bytes:
    """Return the per-user secret, creating it with owner-only permissions on first use."""
    try:
        return bytes.fromhex(path.read_text(encoding="ascii").strip())
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_bytes(_SECRET_BYTES)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another process created it between our read and our create.
        return bytes.fromhex(path.read_text(encoding="ascii").strip())
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(secret.hex())
    return secret


def derive_seed(secret: bytes, run_id: str) -> bytes:
    return hmac.new(secret, run_id.encode("utf-8"), hashlib.sha256).digest()


def parse_seed_hex(text: str) -> bytes:
    """Parse a user-supplied ``--seed HEX`` value."""
    try:
        seed = bytes.fromhex(text.strip())
    except ValueError as exc:
        raise UsageError(f"--seed must be hexadecimal: {exc}") from exc
    if len(seed) < _MIN_SEED_BYTES:
        raise UsageError(
            f"--seed must be at least {_MIN_SEED_BYTES} bytes ({2 * _MIN_SEED_BYTES} hex chars)"
        )
    return seed


def seed_commitment(seed: bytes) -> str:
    return hashlib.sha256(seed).hexdigest()
