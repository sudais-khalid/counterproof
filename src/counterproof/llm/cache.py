"""LLM response cache and test cassettes (PRD section 16).

Responses are keyed by ``sha256(role, prompt_version, provider, model, system, user)``, so a
re-run on the same change is free and deterministic. The same store doubles as a *cassette*:
in ``replay`` mode no provider is ever called and a missing key is an error, which is how
tests and CI run with recorded model output and no API key.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from counterproof.evidence.hashing import canonical_json, pretty_json, sha256_bytes
from counterproof.evidence.store import cache_root

CacheMode = Literal["readwrite", "replay", "record", "off"]
CASSETTE_DIR_ENV: Final = "COUNTERPROOF_LLM_CASSETTES"
CASSETTE_MODE_ENV: Final = "COUNTERPROOF_LLM_MODE"


@dataclass(frozen=True)
class CachedResponse:
    text: str
    tokens_in: int
    tokens_out: int
    model: str


def cache_key(
    *, role: str, prompt_version: str, provider: str, model: str, system: str, user: str
) -> str:
    return sha256_bytes(
        canonical_json(
            {
                "role": role,
                "prompt_version": prompt_version,
                "provider": provider,
                "model": model,
                "system": system,
                "user": user,
            }
        )
    )


class LLMCache:
    def __init__(self, directory: Path, mode: CacheMode = "readwrite") -> None:
        self.directory = directory
        self.mode = mode
        self.hits = 0
        self.misses = 0
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> LLMCache:
        """Cassettes when ``COUNTERPROOF_LLM_CASSETTES`` is set, else the user cache."""
        env = dict(os.environ) if env is None else env
        if cassettes := env.get(CASSETTE_DIR_ENV):
            mode = env.get(CASSETTE_MODE_ENV, "replay")
            if mode not in {"replay", "record", "readwrite"}:
                mode = "replay"
            return cls(Path(cassettes), mode)  # type: ignore[arg-type]
        return cls(cache_root() / "llm-cache", "readwrite")

    def _path(self, key: str) -> Path:
        return self.directory / f"{key[:2]}" / f"{key}.json"

    def get(self, key: str) -> CachedResponse | None:
        if self.mode in {"off", "record"}:
            return None
        path = self._path(key)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            with self._lock:
                self.misses += 1
            return None
        with self._lock:
            self.hits += 1
        return CachedResponse(
            text=str(data["response"]),
            tokens_in=int(data.get("tokens_in", 0)),
            tokens_out=int(data.get("tokens_out", 0)),
            model=str(data.get("model", "")),
        )

    def put(self, key: str, *, request: dict[str, str], response: CachedResponse) -> None:
        if self.mode in {"off", "replay"}:
            return
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "key": key,
            **request,
            "model": response.model,
            "tokens_in": response.tokens_in,
            "tokens_out": response.tokens_out,
            "response": response.text,
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(pretty_json(payload))
        tmp.replace(path)
