"""Directory links for worktrees: a worktree reuses the repository's ``node_modules``.

On Windows a directory junction is used (no administrator rights needed); elsewhere a
symbolic link. Links are always removed *before* a worktree is deleted, so cleanup can never
recurse into the user's real ``node_modules``.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Final

LINKED_DIRS: Final = ("node_modules",)
_REPARSE_POINT: Final = 0x400


def is_dir_link(path: Path) -> bool:
    """A symlink or (Windows) junction, without following it."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & _REPARSE_POINT)


def link_dir(source: Path, target: Path) -> None:
    """Create ``target`` pointing at the existing directory ``source``."""
    if sys.platform == "win32":
        import _winapi  # noqa: PLC0415 - Windows only

        _winapi.CreateJunction(str(source), str(target))
    else:
        target.symlink_to(source, target_is_directory=True)


def unlink_dir_link(path: Path) -> None:
    """Remove a link created by :func:`link_dir`; the directory it points at is untouched."""
    if not is_dir_link(path):
        return
    try:
        path.unlink()
    except OSError:
        path.rmdir()  # junctions are removed like an (empty) directory


def unlink_all(root: Path) -> None:
    for name in LINKED_DIRS:
        unlink_dir_link(root / name)
