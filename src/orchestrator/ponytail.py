from __future__ import annotations

PONYTAIL_SOURCE_REPOSITORY = "https://github.com/DietrichGebert/ponytail"
PONYTAIL_SOURCE_REVISION = "16f29800fd2681bdf24f3eb4ccffe38be3baec6b"

_IMPLEMENTATION_POLICY = """Ponytail full policy:
- Understand the task and trace the real code path before choosing a solution.
- Stop at the first sufficient option: skip unnecessary work; reuse an existing helper
  or pattern; prefer the standard library; prefer a native platform feature; reuse an
  installed dependency; otherwise write the minimum complete implementation.
- Fix the shared root cause rather than one reported symptom. Check callers before
  changing shared behavior.
- Do not add speculative abstractions, future scaffolding, avoidable dependencies, or
  boilerplate. Prefer deletion, boring code, fewer files, and the smallest correct diff.
- Never simplify away trust-boundary validation, data-loss prevention, security,
  accessibility, hardware calibration, an explicit requirement, or the smallest
  runnable check needed for non-trivial logic.
- Commit creation, integration, final deterministic verification, branch push, and Draft
  PR publication are downstream orchestrator responsibilities. Do not perform those
  operations and do not report their absence as an unresolved risk or unmet acceptance
  criterion. Evaluate blocking risks only within this assignment's owned paths and
  responsibilities.
- Stop when the assignment is satisfied. Do not spend tokens explaining alternatives
  that were not chosen.
"""

_REVIEW_POLICY = """Ponytail complexity check:
- Keep the existing correctness, safety, contract, and test review as the primary task.
- Also report only actionable over-engineering: duplicated existing helpers, avoidable
  dependencies, speculative abstractions, dead flexibility, or a materially smaller
  equivalent implementation.
- Do not recommend removing validation, security, data-loss handling, accessibility,
  hardware calibration, explicit requirements, or the smallest useful regression check.
- Commit creation, integration, final deterministic verification, branch push, and Draft
  PR publication are downstream orchestrator responsibilities. Do not perform those
  operations and do not report their absence as a review finding, unresolved risk, or
  unmet acceptance criterion. Review only the integrated implementation and the
  assignment's semantic contract.
- If there is nothing material to remove, add no complexity finding.
"""


def implementation_policy() -> str:
    """Return the pinned, token-bounded Ponytail implementation policy."""
    return _IMPLEMENTATION_POLICY


def review_policy() -> str:
    """Return the compact Ponytail review supplement."""
    return _REVIEW_POLICY


def append_policy(prompt: str, policy: str) -> str:
    """Append one policy block without changing an already complete prompt."""
    normalized = policy.strip()
    if not normalized:
        return prompt
    return f"{prompt.rstrip()}\n\n{normalized}\n"
