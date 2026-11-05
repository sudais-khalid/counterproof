"""FR-CR-02, FR-CR-03, FR-CR-05, FR-CR-06: diff parsing, roles, intent, self-config."""

from __future__ import annotations

import pytest

from counterproof.change.classify import classify
from counterproof.change.diff import changed_new_lines, context_window, parse_unified_diff
from counterproof.change.intent import infer_intent
from counterproof.change.self_config import SELF_CONFIG_FLAG, file_is_self_config
from counterproof.models import ChangedFile, Hunk

DIFF = """\
diff --git a/src/pkg/a.py b/src/pkg/a.py
index 1111111..2222222 100644
--- a/src/pkg/a.py
+++ b/src/pkg/a.py
@@ -3 +3 @@ def f():
-    return 1
+    return 2
@@ -10,0 +11,2 @@ def g():
+    x = 1
+    return x
@@ -20,3 +22,0 @@ def h():
-    a
-    b
-    c
diff --git a/tests/test_new.py b/tests/test_new.py
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/tests/test_new.py
@@ -0,0 +1,2 @@
+def test_x():
+    assert True
diff --git a/old_name.py b/new name.py
similarity index 90%
rename from old_name.py
rename to new name.py
index 4444444..5555555 100644
--- a/old_name.py
+++ b/new name.py
@@ -1 +1 @@
-A = 1
+A = 2
diff --git a/gone.py b/gone.py
deleted file mode 100644
index 6666666..0000000
--- a/gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-x = 1
-y = 2
diff --git a/logo.png b/logo.png
index 7777777..8888888 100644
Binary files a/logo.png and b/logo.png differ
diff --git a/c.py b/c.py
index 9999999..aaaaaaa 100644
--- a/c.py
+++ b/c.py
@@ -1 +1 @@
-x = 1
\\ No newline at end of file
+x = 2
\\ No newline at end of file
"""


def test_fr_cr_02_parses_zero_context_hunks() -> None:
    files = {f.path: f for f in parse_unified_diff(DIFF)}
    a = files["src/pkg/a.py"]
    assert a.status == "modified"
    assert [(h.old_start, h.old_len, h.new_start, h.new_len) for h in a.hunks] == [
        (3, 1, 3, 1),
        (10, 0, 11, 2),
        (20, 3, 22, 0),
    ]
    assert a.hunks[0].lines == ["-    return 1", "+    return 2"]
    assert a.hunks[1].lines == ["+    x = 1", "+    return x"]


def test_fr_cr_02_file_statuses_renames_binaries() -> None:
    files = {f.path: f for f in parse_unified_diff(DIFF)}
    assert files["tests/test_new.py"].status == "added"
    assert files["tests/test_new.py"].hunks[0].new_len == 2
    renamed = files["new name.py"]
    assert (renamed.status, renamed.old_path) == ("renamed", "old_name.py")
    assert files["gone.py"].status == "deleted"
    assert files["gone.py"].hunks[0].path == "gone.py"
    assert files["logo.png"].binary
    assert files["logo.png"].hunks == []


def test_fr_cr_02_no_newline_marker_is_not_a_line() -> None:
    c = next(f for f in parse_unified_diff(DIFF) if f.path == "c.py")
    assert c.hunks[0].lines == ["-x = 1", "+x = 2"]


def test_changed_new_lines_for_pure_deletion_uses_neighbours() -> None:
    hunk = Hunk(path="a.py", old_start=20, old_len=3, new_start=22, new_len=0)
    assert changed_new_lines(hunk) == [22, 23]
    assert changed_new_lines(Hunk(path="a.py", old_start=1, old_len=1, new_start=0, new_len=0)) == [
        1
    ]
    assert changed_new_lines(Hunk(path="a.py", old_start=1, old_len=0, new_start=5, new_len=3)) == [
        5,
        6,
        7,
    ]


def test_fr_cr_02_context_window_default_20_lines() -> None:
    text = "".join(f"line {i}\n" for i in range(1, 101))
    hunk = Hunk(path="a.py", old_start=50, old_len=1, new_start=50, new_len=1)
    first, window = context_window(text, hunk)
    assert first == 30
    assert window.splitlines()[0] == "line 30"
    assert window.splitlines()[-1] == "line 70"
    first, window = context_window(text, hunk, lines=2)
    assert (first, window) == (48, "line 48\nline 49\nline 50\nline 51\nline 52\n")


@pytest.mark.parametrize(
    ("path", "role"),
    [
        ("tests/test_cart.py", "test"),
        ("pkg/cart_test.py", "test"),
        ("tests/conftest.py", "test"),
        ("tests/helpers/factories.py", "test"),
        ("src/cart.test.ts", "test"),
        ("src/cart.spec.tsx", "test"),
        ("src/__tests__/cart.ts", "test"),
        ("src/cart.py", "source"),
        ("src/cart.ts", "source"),
        ("pyproject.toml", "config"),
        ("pytest.ini", "config"),
        ("vitest.config.ts", "config"),
        ("jest.config.js", "config"),
        (".github/workflows/ci.yml", "config"),
        ("requirements-dev.txt", "config"),
        ("README.md", "docs"),
        ("docs/guide.html", "docs"),
        ("CHANGELOG.rst", "docs"),
        ("assets/logo.png", "other"),
        ("data/sample.csv", "other"),
    ],
)
def test_fr_cr_03_classify(path: str, role: str) -> None:
    assert classify(path) == role


def test_fr_cr_03_extra_adapter_matchers() -> None:
    assert classify("checks/verify_cart.py") == "source"
    assert classify("checks/verify_cart.py", [lambda p: p.startswith("checks/")]) == "test"


def _files(*roles: str) -> list[ChangedFile]:
    return [ChangedFile(path=f"f{i}", role=r, status="modified") for i, r in enumerate(roles)]  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("roles", "task", "expected"),
    [
        ((), None, "non_code"),
        (("docs",), "fix the typo", "non_code"),
        (("config", "other"), None, "non_code"),
        (("test",), "fix flaky test", "test_only"),
        (("test", "docs"), None, "test_only"),
        (("source", "test"), "Bug: total is wrong, it should be 30", "bugfix"),
        (("source",), "Refactor pricing into a module, no behaviour change", "refactor"),
        (("source",), "Refactor error handling without behavioural changes", "refactor"),
        (("source",), "Rename helpers for clarity", "refactor"),
        (("source", "test"), "Add support for coupon codes", "feature"),
        (("source",), "Please look at this", "bugfix"),
        (("source",), None, "bugfix"),
    ],
)
def test_fr_cr_05_intent_rules(roles: tuple[str, ...], task: str | None, expected: str) -> None:
    assert infer_intent(_files(*roles), task) == (expected, "rule")


def test_fr_cr_05_user_override_wins() -> None:
    assert infer_intent(_files("docs"), None, "feature") == ("feature", "user")


def test_fr_cr_05_binary_files_do_not_count_as_code() -> None:
    files = [ChangedFile(path="a.py", role="source", status="modified", binary=True)]
    assert infer_intent(files, "fix") == ("non_code", "rule")


def _reader(mapping: dict[str, str | None]):  # type: ignore[no-untyped-def]
    return mapping.get


@pytest.mark.parametrize(
    ("path", "base", "head", "flagged"),
    [
        ("counterproof.toml", "a", "b", True),
        ("pytest.ini", "[pytest]", "[pytest]\naddopts=-x", True),
        ("vitest.config.ts", "x", "y", True),
        (
            "pyproject.toml",
            "[tool.pytest.ini_options]\na=1\n",
            "[tool.pytest.ini_options]\na=2\n",
            True,
        ),
        ("pyproject.toml", "[project]\nname='a'\n", "[project]\nname='b'\n", False),
        ("setup.cfg", "[tool:pytest]\na=1\n", "[tool:pytest]\na=2\n", True),
        ("setup.cfg", "[metadata]\nname=a\n", "[metadata]\nname=b\n", False),
        ("tox.ini", "[pytest]\na=1\n", "[pytest]\na=2\n", True),
        (".github/workflows/ci.yml", "run: pytest", "run: counterproof verify", True),
        (".github/workflows/ci.yml", "run: pytest", "run: pytest -x", False),
        ("package.json", '{"jest": {"a": 1}}', '{"jest": {"a": 2}}', True),
        ("package.json", '{"version": "1"}', '{"version": "2"}', False),
        ("package.json", '{"scripts": {"test": "vitest"}}', '{"scripts": {"test": "true"}}', True),
        ("src/app.py", "a", "b", False),
    ],
)
def test_fr_cr_06_self_config(path: str, base: str, head: str, flagged: bool) -> None:
    assert file_is_self_config(path, _reader({path: base}), _reader({path: head})) is flagged
    assert SELF_CONFIG_FLAG == "self_config_change"
