from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from counterproof.errors import EvidenceError, RunDirError
from counterproof.evidence.hashing import sha256_file
from counterproof.evidence.store import (
    MANIFEST_NAME,
    RUN_ID_RE,
    EvidenceStore,
    cache_root,
    gc_runs,
    new_run_id,
    parse_duration,
    repo_hash,
)
from counterproof.models import ChangeSet, RunManifest, Verdict

HOME = Path("/home/dev")


def test_fr_ws_02_cache_root_uses_xdg_cache_home() -> None:
    root = cache_root({"XDG_CACHE_HOME": "/xdg"}, platform="linux", home=HOME)
    assert root == Path("/xdg") / "counterproof"


def test_fr_ws_02_cache_root_falls_back_to_home_cache() -> None:
    assert cache_root({}, platform="linux", home=HOME) == HOME / ".cache" / "counterproof"


def test_fr_ws_02_cache_root_windows_uses_localappdata() -> None:
    env = {"LOCALAPPDATA": r"C:\Users\dev\AppData\Local", "XDG_CACHE_HOME": "/ignored"}
    root = cache_root(env, platform="win32", home=HOME)
    assert root == Path(r"C:\Users\dev\AppData\Local") / "counterproof"


def test_cache_root_override_env_wins() -> None:
    env = {"COUNTERPROOF_CACHE_DIR": "/custom", "XDG_CACHE_HOME": "/xdg"}
    assert cache_root(env, platform="linux", home=HOME) == Path("/custom")


def test_run_id_format_is_sortable() -> None:
    early = new_run_id(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC), "deadbeef")
    late = new_run_id(datetime(2026, 1, 2, 3, 4, 6, tzinfo=UTC), "00000000")
    assert early == "20260102T030405Z-deadbeef"
    assert RUN_ID_RE.match(new_run_id())
    assert early < late


def test_fr_ws_02_run_dir_layout(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runs = tmp_path / "cache" / "runs"
    store = EvidenceStore.create(repo, "20261104T110000Z-0a1b2c3d", runs_dir=runs)
    assert store.run_dir == runs / repo_hash(repo) / "20261104T110000Z-0a1b2c3d"
    assert store.run_dir.is_dir()
    assert len(repo_hash(repo)) == 16


def test_fr_ws_02_refuses_run_dir_inside_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(RunDirError, match="inside the repository"):
        EvidenceStore.create(repo, runs_dir=repo / ".cache" / "runs")
    assert not (repo / ".cache").exists()


def test_create_refuses_existing_run_and_bad_id(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    runs = tmp_path / "runs"
    EvidenceStore.create(repo, "20261104T110000Z-0a1b2c3d", runs_dir=runs)
    with pytest.raises(RunDirError, match="already exists"):
        EvidenceStore.create(repo, "20261104T110000Z-0a1b2c3d", runs_dir=runs)
    with pytest.raises(RunDirError, match="invalid run id"):
        EvidenceStore.create(repo, "../escape", runs_dir=runs)


def _store(tmp_path: Path, run_id: str = "20261104T110000Z-0a1b2c3d") -> EvidenceStore:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    return EvidenceStore.create(repo, run_id, runs_dir=tmp_path / "runs")


def _manifest(run_id: str) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        tool_version="0.1.0",
        created_at=datetime(2026, 11, 4, tzinfo=UTC),
        seed_commitment="0" * 64,
        config_sha256="1" * 64,
        change=ChangeSet(
            repo_root=Path("/repo"),
            base_sha="a" * 40,
            head_ref="WORKTREE",
            head_snapshot_sha="",
            intent="non_code",
            intent_source="rule",
        ),
        verdict=Verdict.make("INCONCLUSIVE", "NOT_IMPLEMENTED"),
    )


@pytest.mark.parametrize(
    "rel", ["../x", "/abs", "a/../b", "", "a\\b", MANIFEST_NAME, "manifest.sha256"]
)
def test_path_rejects_escapes_and_reserved_names(tmp_path: Path, rel: str) -> None:
    with pytest.raises(EvidenceError):
        _store(tmp_path).path(rel)


def test_seal_hashes_artefacts_and_verify_detects_tampering(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write_text("logs/a.txt", "alpha")
    store.write_json("data.json", {"b": 1, "a": 2})
    store.write_text("secret-ish", "not listed", artefact=False)
    sealed, digest = store.seal(_manifest(store.run_id))

    assert sorted(sealed.artefacts) == ["data.json", "logs/a.txt"]
    assert sealed.artefacts["logs/a.txt"] == sha256_file(store.path("logs/a.txt"))
    assert sha256_file(store.run_dir / MANIFEST_NAME) == digest
    assert store.verify() == []

    store.path("logs/a.txt").write_text("tampered", encoding="utf-8")
    assert store.verify() == ["logs/a.txt"]


def test_seal_is_deterministic(tmp_path: Path) -> None:
    digests = []
    for i in range(2):
        store = _store(tmp_path / str(i))
        store.write_json("data.json", {"z": [1, 2], "a": "x"})
        digests.append(store.seal(_manifest(store.run_id))[1])
    assert digests[0] == digests[1]


def test_open_round_trips_manifest(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write_text("x.txt", "x")
    sealed, _ = store.seal(_manifest(store.run_id))
    reopened = EvidenceStore.open(store.run_dir)
    assert reopened.load_manifest() == sealed
    assert reopened.artefact_paths == ["x.txt"]


def test_track_requires_existing_file(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(EvidenceError, match="missing"):
        store.track("nope.log")


def _age(path: Path, days: float) -> None:
    ts = (datetime.now(UTC) - timedelta(days=days)).timestamp()
    os.utime(path, (ts, ts))


def test_fr_ws_07_gc_removes_only_stale_run_dirs(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    old = runs / "repohash1" / "20260101T000000Z-00000001"
    fresh = runs / "repohash1" / "20261104T000000Z-00000002"
    lonely = runs / "repohash2" / "20260101T000000Z-00000003"
    stranger = runs / "repohash1" / "not-a-run"
    for d in (old, fresh, lonely, stranger):
        d.mkdir(parents=True)
    readonly = old / "obj"
    readonly.write_text("git objects are read-only", encoding="utf-8")
    readonly.chmod(0o444)
    for d in (old, lonely, stranger):
        _age(d, 10)
    _age(fresh, 1)

    assert set(gc_runs(timedelta(days=7), runs_dir=runs, dry_run=True)) == {old, lonely}
    assert old.exists()

    removed = gc_runs(timedelta(days=7), runs_dir=runs)
    assert set(removed) == {old, lonely}
    assert not old.exists()
    assert fresh.exists()
    assert stranger.exists()
    assert not (runs / "repohash2").exists()


def test_gc_on_missing_root_is_a_noop(tmp_path: Path) -> None:
    assert gc_runs(timedelta(days=7), runs_dir=tmp_path / "absent") == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("7d", timedelta(days=7)),
        ("12h", timedelta(hours=12)),
        ("30m", timedelta(minutes=30)),
        ("45s", timedelta(seconds=45)),
    ],
)
def test_parse_duration(text: str, expected: timedelta) -> None:
    assert parse_duration(text) == expected


@pytest.mark.parametrize("text", ["", "7", "d7", "1w", "-1d"])
def test_parse_duration_rejects_garbage(text: str) -> None:
    with pytest.raises(ValueError, match="invalid duration"):
        parse_duration(text)
