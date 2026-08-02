from uuid import uuid4

import pytest
from pydantic import ValidationError

from orchestrator.mcp_schemas import ApproveDeliveryInput, ApprovePublishInput


def test_delivery_commit_message_accepts_project_convention() -> None:
    request = ApproveDeliveryInput(
        run_id=uuid4(),
        expected_version=4,
        commit_message="feat(orchestrator): queue delivery commit",
    )
    assert request.commit_message == "feat(orchestrator): queue delivery commit"


def test_publish_title_accepts_project_convention() -> None:
    request = ApprovePublishInput(
        run_id=uuid4(),
        expected_version=5,
        title="feat(orchestrator): publish delivered branch",
    )
    assert request.title == "feat(orchestrator): publish delivered branch"
    assert request.draft is True


@pytest.mark.parametrize(
    "message",
    [
        "add delivery workflow",
        "feature: add delivery workflow",
        "feat add delivery workflow",
        "feat: first line\nsecond line",
    ],
)
def test_delivery_commit_message_rejects_nonconforming_values(message: str) -> None:
    with pytest.raises(ValidationError):
        ApproveDeliveryInput(
            run_id=uuid4(),
            expected_version=4,
            commit_message=message,
        )


@pytest.mark.parametrize(
    "title",
    [
        "publish pull request",
        "feature: publish pull request",
        "feat publish pull request",
        "feat: first line\nsecond line",
    ],
)
def test_publish_title_rejects_nonconforming_values(title: str) -> None:
    with pytest.raises(ValidationError):
        ApprovePublishInput(
            run_id=uuid4(),
            expected_version=5,
            title=title,
        )
