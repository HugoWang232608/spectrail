from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field, JsonValue

from spectrail.agent.models import AgentModel
from spectrail.agent.planner import AgentOutcome


AGENT_TRACE_RECOVERY_REQUIRED = "AGENT_TRACE_RECOVERY_REQUIRED"
_EVENT_FILE_RE = re.compile(r"^(\d{6})\.json$")

AgentEventType = Literal[
    "profile",
    "planner_request",
    "decision",
    "tool_started",
    "tool_result",
    "policy_rejection",
    "finish",
    "error",
]


class AgentTraceRecoveryError(ValueError):
    pass


class AgentTraceNotFoundError(ValueError):
    pass


class AgentTraceEvent(AgentModel):
    schema_version: Literal["agent_trace_event_v1"] = "agent_trace_event_v1"
    sequence: int = Field(ge=1)
    run_generation: int = Field(ge=1)
    event_type: AgentEventType
    step: int = Field(ge=0)
    planner_request_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    tool: str | None = Field(default=None, max_length=64)
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: datetime


class AgentFinalState(AgentModel):
    schema_version: Literal["agent_final_state_v1"] = "agent_final_state_v1"
    task_id: str = Field(min_length=1, max_length=128)
    run_generation: int = Field(ge=1)
    outcome: AgentOutcome
    steps_used: int = Field(ge=0)
    planner_calls: int = Field(ge=0)
    tool_invocations: int = Field(ge=0)
    pipeline_attempts: int = Field(ge=0)
    final_pipeline_status: str | None = Field(default=None, max_length=64)
    reason: str = Field(min_length=1, max_length=512)


class AgentAttemptSummary(AgentModel):
    schema_version: Literal["agent_attempt_summary_v1"] = "agent_attempt_summary_v1"
    run_generation: int = Field(ge=1)
    attempt: int = Field(ge=1)
    arguments: dict[str, JsonValue]
    pipeline_status: str
    warning_codes: list[str] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    error_code: str | None = None
    started_at: datetime
    completed_at: datetime


class AgentTraceSnapshot(AgentModel):
    schema_version: Literal["agent_trace_snapshot_v1"] = (
        "agent_trace_snapshot_v1"
    )
    task_id: str = Field(min_length=1, max_length=128)
    run_generation: int = Field(ge=1)
    events: list[AgentTraceEvent]
    attempts: list[AgentAttemptSummary]
    final_state: AgentFinalState


def read_agent_trace_snapshot(
    agent_root: str | Path,
    *,
    task_id: str,
    run_generation: int,
) -> AgentTraceSnapshot:
    """Read authoritative Agent artifacts without rebuilding or mutating them."""

    root = Path(agent_root)
    if root.is_symlink():
        _snapshot_recovery_required("Agent trace root is symlinked")
    if not root.exists():
        raise AgentTraceNotFoundError("AGENT_TRACE_NOT_FOUND")
    if not root.is_dir():
        _snapshot_recovery_required("Agent trace root is not a directory")
    for directory in (root, root / "events", root / "attempts"):
        if directory.is_symlink() or not directory.is_dir():
            _snapshot_recovery_required("Agent trace directory is invalid")
        if any(
            path.name.startswith(".") and path.name.endswith(".tmp")
            for path in directory.iterdir()
        ):
            _snapshot_recovery_required("temporary Agent artifact exists")

    events = _read_snapshot_events(root / "events", run_generation)
    attempts = _read_snapshot_attempts(root / "attempts", run_generation)
    final_path = root / "final_state.json"
    if final_path.is_symlink() or not final_path.is_file():
        _snapshot_recovery_required("Agent final state is missing")
    try:
        final_state = AgentFinalState.model_validate(
            json.loads(final_path.read_text(encoding="utf-8"))
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        _snapshot_recovery_required("Agent final state is invalid", cause=exc)
    if (
        final_state.task_id != task_id
        or final_state.run_generation != run_generation
    ):
        _snapshot_recovery_required("Agent final state identity differs")
    _validate_snapshot_consistency(events, attempts, final_state)
    return AgentTraceSnapshot(
        task_id=task_id,
        run_generation=run_generation,
        events=events,
        attempts=attempts,
        final_state=final_state,
    )


def _validate_snapshot_consistency(
    events: list[AgentTraceEvent],
    attempts: list[AgentAttemptSummary],
    final_state: AgentFinalState,
) -> None:
    if not events or events[0].event_type != "profile":
        _snapshot_recovery_required("Agent trace does not start with profile")
    terminal_event = events[-1]
    if terminal_event.event_type not in {"finish", "error"}:
        _snapshot_recovery_required("Agent trace has no terminal event")
    if terminal_event.event_type == "error":
        if final_state.outcome != "failed":
            _snapshot_recovery_required(
                "Agent error terminal differs from final outcome"
            )
        if terminal_event.payload.get("error_code") != final_state.reason:
            _snapshot_recovery_required(
                "Agent error terminal differs from final reason"
            )
    else:
        if terminal_event.payload.get("outcome") != final_state.outcome:
            _snapshot_recovery_required(
                "Agent finish terminal differs from final outcome"
            )
        if terminal_event.payload.get("reason") != final_state.reason:
            _snapshot_recovery_required(
                "Agent finish terminal differs from final reason"
            )
    if final_state.steps_used != sum(
        event.event_type == "decision" for event in events
    ):
        _snapshot_recovery_required("Agent step count differs from events")
    if final_state.planner_calls != sum(
        event.event_type == "planner_request" for event in events
    ):
        _snapshot_recovery_required("Agent planner count differs from events")
    if final_state.tool_invocations != 1 + sum(
        event.event_type == "tool_started" for event in events
    ):
        _snapshot_recovery_required("Agent tool count differs from events")
    if final_state.pipeline_attempts != len(attempts):
        _snapshot_recovery_required("Agent attempt count differs from artifacts")
    last_pipeline_status = (
        attempts[-1].pipeline_status if attempts else None
    )
    if final_state.final_pipeline_status != last_pipeline_status:
        _snapshot_recovery_required(
            "Agent final pipeline status differs from last attempt"
        )


def _read_snapshot_events(
    events_dir: Path,
    run_generation: int,
) -> list[AgentTraceEvent]:
    indexed: list[tuple[int, Path]] = []
    for path in events_dir.iterdir():
        match = _EVENT_FILE_RE.fullmatch(path.name)
        if match is None or path.is_symlink() or not path.is_file():
            _snapshot_recovery_required(
                f"unexpected event artifact: {path.name}"
            )
        indexed.append((int(match.group(1)), path))
    indexed.sort()
    if [sequence for sequence, _ in indexed] != list(
        range(1, len(indexed) + 1)
    ):
        _snapshot_recovery_required("Agent event sequence has a gap")

    events: list[AgentTraceEvent] = []
    try:
        for sequence, path in indexed:
            event = AgentTraceEvent.model_validate(
                json.loads(path.read_text(encoding="utf-8"))
            )
            if (
                event.sequence != sequence
                or event.run_generation != run_generation
            ):
                _snapshot_recovery_required("Agent event identity differs")
            events.append(event)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        if isinstance(exc, AgentTraceRecoveryError):
            raise
        _snapshot_recovery_required("Agent event is invalid", cause=exc)
    return events


def _read_snapshot_attempts(
    attempts_dir: Path,
    run_generation: int,
) -> list[AgentAttemptSummary]:
    attempts: list[AgentAttemptSummary] = []
    for expected, path in enumerate(sorted(attempts_dir.iterdir()), start=1):
        if (
            path.name != f"attempt_{expected:04d}.json"
            or path.is_symlink()
            or not path.is_file()
        ):
            _snapshot_recovery_required("Agent attempt sequence is invalid")
        try:
            summary = AgentAttemptSummary.model_validate(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            _snapshot_recovery_required("Agent attempt is invalid", cause=exc)
        if (
            summary.attempt != expected
            or summary.run_generation != run_generation
        ):
            _snapshot_recovery_required("Agent attempt identity differs")
        attempts.append(summary)
    return attempts


def _snapshot_recovery_required(
    message: str,
    *,
    cause: Exception | None = None,
) -> None:
    error = AgentTraceRecoveryError(
        f"{AGENT_TRACE_RECOVERY_REQUIRED}: {message}"
    )
    if cause is not None:
        raise error from cause
    raise error


class AgentTraceWriter:
    """Publish immutable authoritative events and a rebuildable JSONL view."""

    def __init__(self, agent_root: str | Path, *, run_generation: int) -> None:
        if run_generation < 1:
            raise ValueError("run_generation must be positive")
        self.root = Path(agent_root)
        self.run_generation = run_generation
        self.events_dir = self.root / "events"
        self.attempts_dir = self.root / "attempts"
        self._prepare_directory(self.root)
        self._prepare_directory(self.events_dir)
        self._prepare_directory(self.attempts_dir)
        for directory in (self.root, self.events_dir, self.attempts_dir):
            if any(
                path.name.startswith(".") and path.name.endswith(".tmp")
                for path in directory.iterdir()
            ):
                self._recovery_required("temporary Agent artifact exists")
        self._events = self._load_events()
        self._validate_attempts()

    def append(
        self,
        event_type: AgentEventType,
        *,
        step: int,
        payload: dict[str, JsonValue] | None = None,
        planner_request_fingerprint: str | None = None,
        tool: str | None = None,
    ) -> AgentTraceEvent:
        sequence = len(self._events) + 1
        event = AgentTraceEvent(
            sequence=sequence,
            run_generation=self.run_generation,
            event_type=event_type,
            step=step,
            planner_request_fingerprint=planner_request_fingerprint,
            tool=tool,
            payload=payload or {},
            created_at=_now_utc(),
        )
        target = self.events_dir / f"{sequence:06d}.json"
        _durable_publish_json(target, event.model_dump(mode="json"))
        self._events.append(event)
        self.rebuild_trace()
        return event

    def publish_model(self, filename: str, model: AgentModel) -> Path:
        if filename not in {"policy.json", "profile.json", "final_state.json"}:
            raise ValueError("unsupported Agent snapshot filename")
        target = self.root / filename
        _durable_publish_json(target, model.model_dump(mode="json"))
        return target

    def publish_attempt(self, summary: AgentAttemptSummary) -> Path:
        if summary.run_generation != self.run_generation:
            self._recovery_required("attempt generation differs")
        target = self.attempts_dir / f"attempt_{summary.attempt:04d}.json"
        _durable_publish_json(target, summary.model_dump(mode="json"))
        return target

    def rebuild_trace(self) -> Path:
        target = self.root / "trace.jsonl"
        temporary = self.root / f".trace.jsonl.{uuid4().hex}.tmp"
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                for event in self._events:
                    handle.write(
                        json.dumps(
                            event.model_dump(mode="json"),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    )
                    handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            _fsync_directory(self.root)
        finally:
            if temporary.exists():
                temporary.unlink()
        return target

    @staticmethod
    def _prepare_directory(path: Path) -> None:
        if path.is_symlink():
            raise AgentTraceRecoveryError(
                f"{AGENT_TRACE_RECOVERY_REQUIRED}: symlinked Agent directory"
            )
        if path.exists() and not path.is_dir():
            raise AgentTraceRecoveryError(
                f"{AGENT_TRACE_RECOVERY_REQUIRED}: Agent directory is not a directory"
            )
        path.mkdir(parents=True, exist_ok=True)

    def _load_events(self) -> list[AgentTraceEvent]:
        indexed: list[tuple[int, Path]] = []
        for path in self.events_dir.iterdir():
            match = _EVENT_FILE_RE.fullmatch(path.name)
            if match is None or path.is_symlink() or not path.is_file():
                self._recovery_required(f"unexpected event artifact: {path.name}")
            indexed.append((int(match.group(1)), path))
        indexed.sort()
        expected = list(range(1, len(indexed) + 1))
        if [sequence for sequence, _ in indexed] != expected:
            self._recovery_required("Agent event sequence has a gap")

        events: list[AgentTraceEvent] = []
        try:
            for sequence, path in indexed:
                event = AgentTraceEvent.model_validate(
                    json.loads(path.read_text(encoding="utf-8"))
                )
                if event.sequence != sequence:
                    self._recovery_required("event filename and sequence differ")
                if event.run_generation != self.run_generation:
                    self._recovery_required("event generation differs")
                events.append(event)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            if isinstance(exc, AgentTraceRecoveryError):
                raise
            self._recovery_required("Agent event is invalid", cause=exc)
        return events

    def _validate_attempts(self) -> None:
        paths = sorted(self.attempts_dir.iterdir())
        for expected, path in enumerate(paths, start=1):
            expected_name = f"attempt_{expected:04d}.json"
            if path.name != expected_name or path.is_symlink() or not path.is_file():
                self._recovery_required("Agent attempt sequence is invalid")
            try:
                summary = AgentAttemptSummary.model_validate(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                self._recovery_required("Agent attempt is invalid", cause=exc)
            if summary.attempt != expected or summary.run_generation != self.run_generation:
                self._recovery_required("Agent attempt identity differs")

    @staticmethod
    def _recovery_required(message: str, *, cause: Exception | None = None) -> None:
        error = AgentTraceRecoveryError(
            f"{AGENT_TRACE_RECOVERY_REQUIRED}: {message}"
        )
        if cause is not None:
            raise error from cause
        raise error


def _durable_publish_json(target: Path, payload: object) -> None:
    if target.is_symlink() or target.exists():
        raise AgentTraceRecoveryError(
            f"{AGENT_TRACE_RECOVERY_REQUIRED}: immutable artifact already exists: {target.name}"
        )
    temporary = target.parent / f".{target.name}.{uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)
