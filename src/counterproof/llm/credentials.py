"""API keys: environment first, then ``<user cache>/credentials.toml`` (never the repository).

Keys are never logged, never written to run artefacts, and never reach test processes: the
child-environment scrubber drops every ``*_KEY`` variable (FR-WS-04).
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from counterproof.evidence.store import cache_root

ENV_VARS: Final[dict[str, str]] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}
CREDENTIALS_FILENAME: Final = "credentials.toml"
TEMPLATE: Final = """# Counterproof API keys. This file lives in your user cache directory, outside
# every repository; environment variables (for example DEEPSEEK_API_KEY) take
# precedence.
# Paste a key between the quotes. Never commit this file.

[deepseek]
api_key = ""

[anthropic]
api_key = ""

[openai]
api_key = ""
"""


def credentials_path(cache: Path | None = None) -> Path:
    return (cache if cache is not None else cache_root()) / CREDENTIALS_FILENAME


def ensure_template(path: Path) -> bool:
    """Create an empty template (owner-only) if none exists. Returns True if created."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(TEMPLATE)
    return True


def api_key(
    provider: str, *, env: Mapping[str, str] | None = None, path: Path | None = None
) -> str | None:
    env = os.environ if env is None else env
    var = ENV_VARS.get(provider)
    if var and env.get(var, "").strip():
        return env[var].strip()
    path = path if path is not None else credentials_path()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    section = data.get(provider)
    key = section.get("api_key") if isinstance(section, dict) else None
    return key.strip() if isinstance(key, str) and key.strip() else None
