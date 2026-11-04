from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

import pytest

from counterproof.errors import UsageError
from counterproof.evidence.seed import (
    derive_seed,
    load_or_create_secret,
    parse_seed_hex,
    seed_commitment,
)


def test_fr_mu_05_seed_is_hmac_of_secret_and_run_id() -> None:
    secret = b"k" * 32
    expected = hmac.new(secret, b"run-1", hashlib.sha256).digest()
    assert derive_seed(secret, "run-1") == expected
    assert derive_seed(secret, "run-1") != derive_seed(secret, "run-2")
    assert derive_seed(secret, "run-1") != derive_seed(b"j" * 32, "run-1")


def test_secret_is_created_once_and_reused(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "secret.key"
    first = load_or_create_secret(path)
    second = load_or_create_secret(path)
    assert first == second
    assert len(first) == 32


def test_secrets_differ_between_users(tmp_path: Path) -> None:
    assert load_or_create_secret(tmp_path / "a") != load_or_create_secret(tmp_path / "b")


def test_seed_commitment_is_sha256_of_seed() -> None:
    seed = bytes(range(32))
    assert seed_commitment(seed) == hashlib.sha256(seed).hexdigest()


def test_parse_seed_hex_accepts_valid_hex() -> None:
    assert parse_seed_hex(" 00ff00ff00ff00ff ") == bytes.fromhex("00ff00ff00ff00ff")


@pytest.mark.parametrize("text", ["xyz", "abc", "00ff"])
def test_parse_seed_hex_rejects_bad_or_short_input(text: str) -> None:
    with pytest.raises(UsageError, match="--seed"):
        parse_seed_hex(text)
