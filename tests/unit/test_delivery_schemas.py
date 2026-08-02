from uuid import uuid4

import pytest
from pydantic import ValidationError

from orchestrator.mcp_schemas import ApproveDeliveryInput


def test_delivery_commit_message_accepts_project_convention() -> None:
    request = ApproveDeliveryInput(
        run_id=uuid4(),
        expected_version=4,
        commit_message="feat(orchestrator): queue delivery commit",
    )
    assert request.commit_message == "feat(orchestrator): queue delivery commit"


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
