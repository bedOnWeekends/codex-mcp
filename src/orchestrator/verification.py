from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .schemas import VerificationCommandSpec


@dataclass(frozen=True, slots=True)
class CommandResult:
    name: str
    command: list[str]
    returncode: int | None
    output: str
    timed_out: bool = False

    @property
    def success(self) -> bool:
        return not self.timed_out and self.returncode == 0

    @property
    def display_command(self) -> str:
        return subprocess.list2cmdline(self.command)


@dataclass(frozen=True, slots=True)
class VerificationResult:
    commands: list[CommandResult]

    @property
    def success(self) -> bool:
        return all(item.success for item in self.commands)

    @property
    def commands_run(self) -> list[str]:
        return [item.display_command for item in self.commands]

    def summary(self) -> str:
        sections: list[str] = []
        for item in self.commands:
            if item.timed_out:
                status = "TIMED OUT"
            elif item.returncode == 0:
                status = "PASSED"
            else:
                status = f"FAILED ({item.returncode})"
            sections.append(f"## {item.name}: {status}\nCommand: `{item.display_command}`\n\n```text\n{item.output.rstrip()}\n```")
        return "\n\n".join(sections) or "No verification commands were run."


class VerificationRunner:
    """Runs administrator-registered commands without invoking a shell."""

    def __init__(self, *, default_timeout_seconds: int) -> None:
        self.default_timeout_seconds = default_timeout_seconds

    async def run(self, *, cwd: Path, configured_commands: list[VerificationCommandSpec]) -> VerificationResult:
        commands = [VerificationCommandSpec(name="git diff check", command=["git", "diff", "--check"], timeout_seconds=self.default_timeout_seconds), *configured_commands]
        results: list[CommandResult] = []
        for spec in commands:
            result = await self._run_one(cwd=cwd, spec=spec)
            results.append(result)
            if not result.success:
                break
        return VerificationResult(commands=results)

    async def _run_one(self, *, cwd: Path, spec: VerificationCommandSpec) -> CommandResult:
        environment = os.environ.copy()
        environment.update(spec.env)
        process = await asyncio.create_subprocess_exec(*spec.command, cwd=str(cwd), env=environment, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=spec.timeout_seconds)
        except TimeoutError:
            process.kill()
            stdout, _ = await process.communicate()
            return CommandResult(name=spec.name, command=spec.command, returncode=None, output=stdout.decode(errors="replace"), timed_out=True)
        return CommandResult(name=spec.name, command=spec.command, returncode=process.returncode, output=stdout.decode(errors="replace"))
