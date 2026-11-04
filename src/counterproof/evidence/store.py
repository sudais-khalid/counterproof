"""Run directory and evidence bundle (FR-WS-02, FR-WS-07, PRD sections 6.3 and 10).

Every run gets its own directory under the user cache, never inside the repository. Files
written through :class:`EvidenceStore` are tracked as artefacts; :meth:`EvidenceStore.seal`
hashes them into the manifest and writes ``manifest.json`` plus its own digest.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import stat
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Final

from pydantic import BaseModel, ValidationError

from counterproof.errors import EvidenceError, RunDirError
from counterproof.evidence.hashing import pretty_json, sha256_bytes, sha256_file
from counterproof.models import RunManifest

CACHE_DIR_ENV: Final = "COUNTERPROOF_CACHE_DIR"
MANIFEST_NAME: Final = "manifest.json"
MANIFEST_DIGEST_NAME: Final = "manifest.sha256"
RUN_ID_RE: Final = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")


def cache_root(
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> Path:
    """FR-WS-02: ``${XDG_CACHE_HOME:-~/.cache}/counterproof`` (Windows: ``%LOCALAPPDATA%``).

    ``COUNTERPROOF_CACHE_DIR`` overrides both, which keeps tests out of the real cache.
    """
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    home = Path.home() if home is None else home
    if override := env.get(CACHE_DIR_ENV):
        return Path(override)
    if platform == "win32":
        base = env.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
    else:
        base = env.get("XDG_CACHE_HOME") or str(home / ".cache")
    return Path(base) / "counterproof"


def runs_root(cache: Path | None = None) -> Path:
    return (cache if cache is not None else cache_root()) / "runs"


def repo_hash(repo_root: Path) -> str:
    """Stable short id for a repository, from its normalised absolute path."""
    normalised = os.path.normcase(str(repo_root.resolve()))
    return sha256_bytes(normalised.encode("utf-8"))[:16]


def new_run_id(now: datetime | None = None, token: str | None = None) -> str:
    """Sortable run id: ``YYYYMMDDTHHMMSSZ-<8 hex>``."""
    now = now or datetime.now(UTC)
    token = token or secrets.token_hex(4)
    return f"{now.astimezone(UTC):%Y%m%dT%H%M%SZ}-{token}"


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


class EvidenceStore:
    """Owns one run directory. All writes go through here so they can be hashed."""

    def __init__(self, run_dir: Path, run_id: str) -> None:
        self.run_dir = run_dir
        self.run_id = run_id
        self._artefacts: set[str] = set()

    @classmethod
    def create(
        cls,
        repo_root: Path,
        run_id: str | None = None,
        *,
        runs_dir: Path | None = None,
    ) -> EvidenceStore:
        """Create ``<runs>/<repo-hash>/<run-id>/``, refusing any location inside the repo."""
        run_id = run_id or new_run_id()
        if not RUN_ID_RE.match(run_id):
            raise RunDirError(f"invalid run id {run_id!r}")
        root = runs_dir if runs_dir is not None else runs_root()
        run_dir = root / repo_hash(repo_root) / run_id
        # FR-WS-02: never inside the repository.
        if _is_within(run_dir, repo_root):
            raise RunDirError(
                f"run directory {run_dir} is inside the repository {repo_root}; "
                f"set {CACHE_DIR_ENV} to a location outside it"
            )
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise RunDirError(f"run directory already exists: {run_dir}") from exc
        except OSError as exc:
            raise RunDirError(f"cannot create run directory {run_dir}: {exc}") from exc
        return cls(run_dir, run_id)

    @classmethod
    def open(cls, run_dir: Path) -> EvidenceStore:
        if not (run_dir / MANIFEST_NAME).is_file():
            raise EvidenceError(f"no {MANIFEST_NAME} in {run_dir}")
        store = cls(run_dir, run_dir.name)
        store._artefacts = set(store.load_manifest().artefacts)
        return store

    # -- writing -----------------------------------------------------------------------

    def path(self, rel: str) -> Path:
        """Resolve a run-relative POSIX path, rejecting anything that escapes the run dir."""
        pure = PurePosixPath(rel)
        if pure.is_absolute() or not pure.parts or ".." in pure.parts or "\\" in rel:
            raise EvidenceError(f"artefact path must be relative and inside the run: {rel!r}")
        if rel in {MANIFEST_NAME, MANIFEST_DIGEST_NAME}:
            raise EvidenceError(f"{rel} is reserved")
        return self.run_dir.joinpath(*pure.parts)

    def write_bytes(self, rel: str, data: bytes, *, artefact: bool = True) -> str:
        """Atomically write ``data``; returns its sha256."""
        target = self.path(rel)
        _atomic_write(target, data)
        if artefact:
            self._artefacts.add(rel)
        return sha256_bytes(data)

    def write_text(self, rel: str, text: str, *, artefact: bool = True) -> str:
        return self.write_bytes(rel, text.encode("utf-8"), artefact=artefact)

    def write_json(self, rel: str, obj: BaseModel | Any, *, artefact: bool = True) -> str:
        payload = obj.model_dump(mode="json") if isinstance(obj, BaseModel) else obj
        return self.write_bytes(rel, pretty_json(payload), artefact=artefact)

    def track(self, rel: str) -> None:
        """Register a file that was written directly (for example a log or junit file)."""
        if not self.path(rel).is_file():
            raise EvidenceError(f"cannot track missing artefact {rel}")
        self._artefacts.add(rel)

    @property
    def artefact_paths(self) -> list[str]:
        return sorted(self._artefacts)

    # -- sealing -----------------------------------------------------------------------

    def seal(self, manifest: RunManifest) -> tuple[RunManifest, str]:
        """Hash all artefacts into the manifest, write it, and return it with its digest."""
        artefacts = {rel: sha256_file(self.path(rel)) for rel in self.artefact_paths}
        sealed = manifest.model_copy(update={"artefacts": artefacts})
        data = pretty_json(sealed.model_dump(mode="json"))
        digest = sha256_bytes(data)
        _atomic_write(self.run_dir / MANIFEST_NAME, data)
        _atomic_write(self.run_dir / MANIFEST_DIGEST_NAME, f"{digest}  {MANIFEST_NAME}\n".encode())
        return sealed, digest

    def load_manifest(self) -> RunManifest:
        path = self.run_dir / MANIFEST_NAME
        try:
            return RunManifest.model_validate_json(path.read_bytes())
        except (OSError, ValidationError) as exc:
            raise EvidenceError(f"cannot load {path}: {exc}") from exc

    def verify(self) -> list[str]:
        """Return artefacts (and the manifest itself) whose bytes no longer match."""
        problems: list[str] = []
        manifest_path = self.run_dir / MANIFEST_NAME
        digest_path = self.run_dir / MANIFEST_DIGEST_NAME
        recorded = digest_path.read_text(encoding="utf-8").split()[0]
        if sha256_file(manifest_path) != recorded:
            problems.append(MANIFEST_NAME)
        for rel, expected in self.load_manifest().artefacts.items():
            target = self.path(rel)
            if not target.is_file() or sha256_file(target) != expected:
                problems.append(rel)
        return problems


def _atomic_write(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{secrets.token_hex(4)}.tmp")
    try:
        tmp.write_bytes(data)
        tmp.replace(target)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise EvidenceError(f"cannot write {target}: {exc}") from exc


def _make_writable_and_retry(func: Callable[..., Any], path: str, _exc: object) -> None:
    # git object files are read-only on Windows; rmtree needs them writable.
    Path(path).chmod(stat.S_IWRITE | stat.S_IREAD)
    func(path)


def remove_tree(path: Path) -> None:
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_make_writable_and_retry)
    else:  # pragma: no cover - exercised on 3.11 CI only
        shutil.rmtree(path, onerror=_make_writable_and_retry)


def gc_runs(
    older_than: timedelta,
    *,
    runs_dir: Path | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
) -> list[Path]:
    """FR-WS-07: remove run directories whose last modification is older than ``older_than``.

    Only directories shaped like ``<repo-hash>/<run-id>`` are touched.
    """
    root = runs_dir if runs_dir is not None else runs_root()
    if not root.is_dir():
        return []
    cutoff = (now or datetime.now(UTC)) - older_than
    removed: list[Path] = []
    for repo_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for run_dir in sorted(p for p in repo_dir.iterdir() if p.is_dir()):
            if not RUN_ID_RE.match(run_dir.name):
                continue
            mtime = datetime.fromtimestamp(run_dir.stat().st_mtime, UTC)
            if mtime < cutoff:
                if not dry_run:
                    remove_tree(run_dir)
                removed.append(run_dir)
        if not dry_run and not any(repo_dir.iterdir()):
            repo_dir.rmdir()
    return removed


def parse_duration(text: str) -> timedelta:
    """Parse ``7d``, ``12h``, ``30m`` or ``45s`` for ``gc --older-than``."""
    match = re.fullmatch(r"\s*(\d+)\s*([dhms])\s*", text)
    if not match:
        raise ValueError(f"invalid duration {text!r}; use e.g. 7d, 12h, 30m")
    value, unit = int(match.group(1)), match.group(2)
    return {
        "d": timedelta(days=value),
        "h": timedelta(hours=value),
        "m": timedelta(minutes=value),
        "s": timedelta(seconds=value),
    }[unit]
