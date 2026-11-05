"""Mutant selection (FR-MU-05): plausibility, diversity, seeded randomness.

Rule mutants carry a per-operator prior instead of an LLM plausibility score. Operator
usefulness history (PRD open question) is not used in v1. Candidates are shuffled with the
per-run seed, ordered by score (stable, so the shuffle breaks ties), then taken greedily with
at most two mutants per line until the budget is reached.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Sequence
from typing import Final, TypeVar

from counterproof.probes.mutants.rule_forge_py import Candidate

MAX_PER_LINE: Final = 2
C = TypeVar("C", bound=Candidate)


def seed_int(seed: bytes) -> int:
    return int.from_bytes(seed[:8], "big")


def select_mutants(candidates: Sequence[C], budget: int, seed: bytes) -> list[C]:
    # Sort first so the shuffle does not depend on the order generation happened to use.
    pool = sorted(candidates, key=lambda c: (c.file, c.start_line, c.operator, c.replacement))
    # Seeded on purpose: the secret is in the seed, not the generator.
    random.Random(seed_int(seed)).shuffle(pool)  # noqa: S311
    pool.sort(key=lambda c: -c.prior)
    chosen: list[C] = []
    per_line: Counter[tuple[str, int]] = Counter()
    for candidate in pool:
        if len(chosen) >= budget:
            break
        key = (candidate.file, candidate.line)
        if per_line[key] >= MAX_PER_LINE:
            continue
        per_line[key] += 1
        chosen.append(candidate)
    return sorted(chosen, key=lambda c: (c.file, c.start_line, c.operator, c.replacement))
