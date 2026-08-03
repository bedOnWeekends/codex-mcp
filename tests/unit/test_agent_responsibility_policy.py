from __future__ import annotations

from orchestrator.ponytail import implementation_policy, review_policy


def test_implementation_policy_keeps_publication_out_of_agent_risks() -> None:
    policy = implementation_policy()

    assert "downstream orchestrator responsibilities" in policy
    assert "Draft PR publication" in policy
    assert "do not report their absence as an unresolved risk" in policy
    assert "owned paths and responsibilities" in policy


def test_review_policy_keeps_publication_out_of_review_findings() -> None:
    policy = review_policy()

    assert "downstream orchestrator responsibilities" in policy
    assert "Draft PR publication" in policy
    assert "do not report their absence as a review finding" in policy
    assert "integrated implementation" in policy
