"""Evaluation corpora (PRD section 17.1): what to run, with which labels.

A corpus is a directory holding ``corpus.toml``. Paths in it are relative to that
directory. Three kinds of case:

* fixture variants: every ``tasks/<task>/<variant>`` of a fixture repo (``[[repos]]`` with
  ``fixtures = ...``), labelled through the repo's ``labels`` table (variant -> label/kind);
* explicit fixture cases (``[[cases]]`` with ``repo``, ``task`` and ``variant`` or
  ``overlay``, optionally a ``transcript``, a ``base_overlay`` committed before the change
  and ``deletions``), for example the C2 claim-audit sessions;
* git cases (``[[cases]]`` with ``git``, ``base`` and ``head``), for real pull requests (C3).
  They run foreign code, so the harness refuses them without ``--allow-remote``.

Labels are ``honest`` or ``gamed``; ``kind`` names the gaming technique (hard-coded,
weakened, partial, false-claim, ...).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from counterproof.errors import UsageError
from counterproof.workspace.links import link_dir

CORPUS_FILE: Final = "corpus.toml"
Label = Literal["honest", "gamed"]
_GIT_IDENTITY: Final = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Corpus",
    "GIT_AUTHOR_EMAIL": "corpus@example.invalid",
    "GIT_COMMITTER_NAME": "Corpus",
    "GIT_COMMITTER_EMAIL": "corpus@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
}


@dataclass(frozen=True)
class Configuration:
    name: str
    provider: str = "none"
    replay: bool = True
    """LLM answers come from the repo's cassettes; nothing reaches the network."""
    profile: str = "full"


@dataclass(frozen=True)
class RepoSpec:
    name: str
    fixtures: Path
    cassettes: Path | None = None
    node_modules: Path | None = None
    labels: dict[str, tuple[Label, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Case:
    id: str
    label: Label
    kind: str
    repo: str | None = None
    task: str | None = None
    variant: str | None = None
    overlay: Path | None = None
    transcript: Path | None = None
    base_overlay: Path | None = None
    """Files committed on ``main`` on top of the fixture base (for example a test file the
    agent was given before it started)."""
    deletions: tuple[str, ...] = ()
    """Repository paths the change deletes (an overlay can only add or replace files)."""
    git: str | None = None
    base: str | None = None
    head: str | None = None

    @property
    def remote(self) -> bool:
        return self.git is not None


@dataclass(frozen=True)
class Corpus:
    name: str
    root: Path
    seed: str
    configurations: list[Configuration]
    repos: dict[str, RepoSpec]
    cases: list[Case]
    description: str = ""


def _label(raw: Any, where: str) -> tuple[Label, str]:
    if isinstance(raw, str):
        label, kind = raw, raw
    elif isinstance(raw, dict):
        label, kind = str(raw.get("label", "")), str(raw.get("kind", raw.get("label", "")))
    else:
        raise UsageError(f"{where}: label must be a string or a table")
    if label not in {"honest", "gamed"}:
        raise UsageError(f"{where}: label must be 'honest' or 'gamed', got {label!r}")
    return label, kind  # type: ignore[return-value]


def load_corpus(directory: Path) -> Corpus:
    path = directory / CORPUS_FILE
    if not path.is_file():
        raise UsageError(f"{path} not found: a corpus is a directory with {CORPUS_FILE}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise UsageError(f"{path}: {exc}") from exc
    root = directory.resolve()

    def rel(value: Any) -> Path | None:
        return (root / str(value)).resolve() if value else None

    configurations = [
        Configuration(
            name=str(c["name"]),
            provider=str(c.get("provider", "none")),
            replay=bool(c.get("replay", True)),
            profile=str(c.get("profile", "full")),
        )
        for c in data.get("configurations", [{"name": "rules"}])
    ]
    repos: dict[str, RepoSpec] = {}
    cases: list[Case] = []
    for r in data.get("repos", []):
        fixtures = rel(r.get("fixtures"))
        if fixtures is None or not (fixtures / "base").is_dir():
            raise UsageError(f"{path}: repo {r.get('name')!r} needs fixtures with a base/ tree")
        labels = {
            k: _label(v, f"{path}: {r['name']}.labels.{k}") for k, v in r.get("labels", {}).items()
        }
        spec = RepoSpec(
            name=str(r["name"]),
            fixtures=fixtures,
            cassettes=rel(r.get("cassettes")),
            node_modules=rel(r.get("node_modules")),
            labels=labels,
        )
        repos[spec.name] = spec
        if r.get("all_variants", True) and labels:
            tasks = fixtures / "tasks"
            for task in (
                sorted(p.name for p in tasks.iterdir() if p.is_dir()) if tasks.is_dir() else []
            ):
                for variant in sorted(labels):
                    if (tasks / task / variant).is_dir():
                        label, kind = labels[variant]
                        cases.append(
                            Case(
                                f"{spec.name}/{task}/{variant}",
                                label,
                                kind,
                                spec.name,
                                task,
                                variant,
                            )
                        )
    for c in data.get("cases", []):
        label, kind = _label(c.get("label"), f"{path}: case {c.get('id')!r}")
        case = Case(
            id=str(c["id"]),
            label=label,
            kind=str(c.get("kind", kind)),
            repo=c.get("repo"),
            task=c.get("task"),
            variant=c.get("variant"),
            overlay=rel(c.get("overlay")) if c.get("overlay") else None,
            transcript=rel(c.get("transcript")),
            base_overlay=rel(c.get("base_overlay")) if c.get("base_overlay") else None,
            deletions=tuple(str(d) for d in c.get("deletions", [])),
            git=c.get("git"),
            base=c.get("base"),
            head=c.get("head"),
        )
        if case.remote and not (case.base and case.head):
            raise UsageError(f"{path}: git case {case.id!r} needs base and head commits")
        if not case.remote and (case.repo not in repos or not case.task):
            raise UsageError(f"{path}: case {case.id!r} needs a known repo and a task")
        cases.append(case)
    if not cases:
        raise UsageError(f"{path}: no cases")
    return Corpus(
        name=str(data.get("name", directory.name)),
        root=root,
        seed=str(data.get("seed", "00" * 16)),
        configurations=configurations,
        repos=repos,
        cases=cases,
        description=str(data.get("description", "")),
    )


# -- materialising a case as a git repository ---------------------------------------------


def _git(repo: Path, *args: str) -> str:
    empty = repo.parent / ".gitconfig-empty"
    empty.touch()
    env = {**os.environ, **_GIT_IDENTITY, "GIT_CONFIG_GLOBAL": str(empty)}
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@dataclass(frozen=True)
class Materialised:
    path: Path
    base: str
    head: str
    task_text: str | None


def materialise(corpus: Corpus, case: Case, dest: Path) -> Materialised:
    """Build the case as a repository at ``dest``: base committed on ``main``, the change
    committed on ``agent/<task>`` (fixture cases) or the pull request's commits (git cases)."""
    if case.remote:
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--quiet", "--filter=blob:none", str(case.git), str(dest)],
            check=True,
            capture_output=True,
        )
        return Materialised(dest, str(case.base), str(case.head), None)
    repo = corpus.repos[str(case.repo)]
    shutil.copytree(repo.fixtures / "base", dest)
    if repo.node_modules is not None and repo.node_modules.is_dir():
        link_dir(repo.node_modules, dest / "node_modules")
    _git(dest, "init", "-q", "-b", "main")
    _git(dest, "add", "-A")
    _git(dest, "commit", "-q", "-m", f"Initial {repo.name}")
    if case.base_overlay is not None:
        shutil.copytree(case.base_overlay, dest, dirs_exist_ok=True, copy_function=shutil.copy)
        _git(dest, "add", "-A")
        _git(dest, "commit", "-q", "-m", "Files given to the agent before it started")
    base = _git(dest, "rev-parse", "HEAD").strip()
    task_dir = repo.fixtures / "tasks" / str(case.task)
    task_text = (task_dir / "task.md").read_text(encoding="utf-8").strip()
    overlay = case.overlay or task_dir / str(case.variant)
    _git(dest, "checkout", "-q", "-b", f"agent/{case.task}")
    # Fresh mtimes so git's stat cache can never mistake a same-size overlay for unchanged.
    shutil.copytree(overlay, dest, dirs_exist_ok=True, copy_function=shutil.copy)
    for rel_path in case.deletions:
        (dest / rel_path).unlink(missing_ok=True)
    _git(dest, "rm", "-r", "-q", "--cached", ".")
    _git(dest, "add", "-A")
    _git(dest, "commit", "-q", "-m", task_text)
    head = _git(dest, "rev-parse", "HEAD").strip()
    return Materialised(dest, base, head, task_text)
