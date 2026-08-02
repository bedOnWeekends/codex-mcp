from __future__ import annotations

import asyncio
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from .schemas import ArtifactCreate, ArtifactKind


class ArtifactWriter:
    """Writes immutable local artifacts below the configured runtime directory."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    async def write_text(
        self,
        *,
        run_id: UUID,
        task_id: UUID,
        kind: ArtifactKind,
        filename: str,
        content: str,
    ) -> ArtifactCreate:
        safe_name = Path(filename).name
        if safe_name != filename or not safe_name:
            raise ValueError("artifact filename must be a plain file name")
        directory = self.root / str(run_id) / str(task_id)
        path = directory / safe_name
        data = content.encode("utf-8")

        def write() -> None:
            directory.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_bytes(data)
            temporary.replace(path)

        await asyncio.to_thread(write)
        return ArtifactCreate(
            run_id=run_id,
            task_id=task_id,
            kind=kind,
            path=path,
            sha256=sha256(data).hexdigest(),
            size_bytes=len(data),
        )
