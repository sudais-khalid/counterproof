"""Record real LLM responses for the C1 golden tests (run by a human, needs an API key).

    uv run python tests/golden/record_cassettes.py --provider deepseek [--repo ts_cart]

Every C1 variant of the fixture repo is built and verified with the given provider in
*record* mode; responses land in tests/cassettes/<repo>/ keyed by prompt, so the golden
tests replay them offline.
A hard total spend cap (default $2) stops the recording, and each run keeps its own
BudgetGuard. Prompts contain only fixture code and task text.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SEED = "00" * 16


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="deepseek")
    parser.add_argument("--repo", default="py_ledger", choices=["py_ledger", "ts_cart"])
    parser.add_argument("--profile", default="full")
    parser.add_argument("--max-total-usd", type=float, default=2.0)
    parser.add_argument("--llm-budget", type=int, default=None, help="mutants.llm_budget override")
    parser.add_argument("--keep-runs", type=Path, default=None, help="copy run dirs here")
    parser.add_argument(
        "--mode",
        default="record",
        choices=["record", "readwrite"],
        help="record: call the model for every prompt; readwrite: reuse recorded replies",
    )
    args = parser.parse_args()

    from counterproof.llm.credentials import ENV_VARS, api_key  # noqa: PLC0415

    # Resolve the key from the real user cache *before* redirecting the cache directory.
    key = api_key(args.provider)
    if key is None and args.provider in ENV_VARS:
        print(f"no API key for {args.provider}: set {ENV_VARS[args.provider]} or credentials.toml")
        return 2
    if key is not None:
        os.environ[ENV_VARS[args.provider]] = key

    tmp = Path(tempfile.mkdtemp(prefix="cp-record-"))
    os.environ["COUNTERPROOF_CACHE_DIR"] = str(args.keep_runs or tmp / "cache")
    os.environ["COUNTERPROOF_PYTHON"] = sys.executable
    cassettes = ROOT / "tests" / "cassettes" / args.repo
    os.environ["COUNTERPROOF_LLM_CASSETTES"] = str(cassettes)
    os.environ["COUNTERPROOF_LLM_MODE"] = args.mode

    from tests.helpers import VARIANTS, build_repo, load_expectations  # noqa: PLC0415

    from counterproof.orchestrator import VerifyRequest, run_verify  # noqa: PLC0415

    tasks = load_expectations()["repos"][args.repo]["tasks"]
    total = 0.0
    for task in sorted(tasks):
        for variant in VARIANTS:
            built = build_repo(tmp / f"{task}-{variant}", args.repo, task=task, variant=variant)
            out = run_verify(
                VerifyRequest(
                    cwd=built.path,
                    seed_hex=SEED,
                    overrides={
                        "llm": {"provider": args.provider},
                        "run": {"profile": args.profile},
                        **({"mutants": {"llm_budget": args.llm_budget}} if args.llm_budget else {}),
                    },
                )
            )
            usage = out.manifest.llm
            spent = usage.usd if usage else 0.0
            total += spent
            verdict = out.manifest.verdict
            print(
                f"{task:24} {variant:9} {verdict.verdict:12} {verdict.rule_id:20} "
                f"calls={usage.calls if usage else 0} ${spent:.4f} total=${total:.4f} "
                f"errors={usage.errors if usage else []} note={usage.note if usage else ''}",
                flush=True,
            )
            if total > args.max_total_usd:
                print(f"stopping: total spend ${total:.2f} over cap ${args.max_total_usd:.2f}")
                return 1
    print(f"done: ${total:.4f}; cassettes in {cassettes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
