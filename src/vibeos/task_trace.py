from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, is_dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from .audit import default_audit_path
from .models import utc_now_iso


TRACE_MAX_STRING_CHARS = 2_048
_SENSITIVE_TRACE_KEYS = {
    "api_key",
    "authorization",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
}
_CONTENT_TRACE_KEYS = {
    "content",
    "raw_output",
    "response_payload",
    "request_payload",
    "supplemental_input",
    "utterance",
}


def default_trace_root() -> Path:
    return default_audit_path().with_name("runs")


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _trace_payload(value: Any, *, allow_content: bool) -> Any:
    """Serialize trace data with bounded sensitive-data retention.

    Normal traces retain structure and operational metadata but omit raw user
    and provider content. Debug traces may retain content for diagnosis, while
    credentials are always redacted and large strings are always truncated.
    """

    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).lower()
            if normalized_key in _SENSITIVE_TRACE_KEYS or any(token in normalized_key for token in _SENSITIVE_TRACE_KEYS):
                sanitized[str(key)] = "[REDACTED]"
            elif not allow_content and normalized_key in _CONTENT_TRACE_KEYS:
                sanitized[str(key)] = "[OMITTED]"
            else:
                sanitized[str(key)] = _trace_payload(item, allow_content=allow_content)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [_trace_payload(item, allow_content=allow_content) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str):
        if len(value) <= TRACE_MAX_STRING_CHARS:
            return value
        return value[:TRACE_MAX_STRING_CHARS] + "...[TRUNCATED]"
    return value


def _safe_name(value: str, *, fallback: str = "artifact") -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value.strip().lower())
    cleaned = cleaned.strip("_")
    return cleaned or fallback


def _references_reused_artifacts(consumed_artifacts: dict[str, Any] | None) -> bool:
    if not consumed_artifacts:
        return False
    artifact_keys = (
        "understanding_id",
        "candidate_set_id",
        "route_decision_id",
        "replan_decision_id",
        "semantic_summary_id",
        "semantic_acceptance_decision_id",
    )
    for key in artifact_keys:
        value = consumed_artifacts.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def make_trace_run_id(seed: str) -> str:
    digest = sha256(f"{seed}:{utc_now_iso()}".encode("utf-8")).hexdigest()[:12]
    return f"run_{digest}"


class TaskTraceSession:
    trace_version = "v0.1"

    def __init__(
        self,
        *,
        root: Path,
        run_id: str,
        command_name: str,
        utterance: str,
        mode: str,
        transport: str | None,
        dry_run: bool,
        debug: bool,
        review_id: str | None = None,
    ) -> None:
        self.root = root
        self.run_id = run_id
        self.debug = debug
        self.started_at = utc_now_iso()
        self.run_dir = self.root / self.started_at[:10] / run_id
        self.artifacts_dir = self.run_dir / "artifacts"
        self._event_count = 0
        self._model_io_count = 0
        self._primary_understanding_call_count = 0
        self._full_context_call_count = 0
        self._model_reparse_count = 0
        self._structured_followup_call_count = 0
        self._artifact_reuse_count = 0
        self._semantic_summary_cache_hit_count = 0
        self._escalation_count = 0
        self._model_call_kinds: dict[str, int] = {}
        try:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.root = Path.cwd() / ".vibeos" / "runs"
            self.run_dir = self.root / self.started_at[:10] / run_id
            self.artifacts_dir = self.run_dir / "artifacts"
            self.run_dir.mkdir(parents=True, exist_ok=True)
            self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        trace_utterance = _trace_payload(utterance, allow_content=True) if debug else None
        utterance_digest = sha256(utterance.encode("utf-8")).hexdigest()
        self._write_json(
            self.run_dir / "manifest.json",
            {
                "trace_version": self.trace_version,
                "run_id": run_id,
                "command": command_name,
                "utterance": trace_utterance,
                "utterance_sha256": utterance_digest,
                "utterance_length": len(utterance),
                "mode": mode,
                "transport": transport,
                "dry_run": dry_run,
                "debug": debug,
                "review_id": review_id,
                "started_at": self.started_at,
                "status": "running",
            },
        )
        self._write_json(
            self.run_dir / "summary.json",
            {
                "trace_version": self.trace_version,
                "run_id": run_id,
                "command": command_name,
                "utterance": trace_utterance,
                "utterance_sha256": utterance_digest,
                "utterance_length": len(utterance),
                "transport": transport,
                "dry_run": dry_run,
                "started_at": self.started_at,
                "status": "running",
                "event_count": 0,
                "model_io_count": 0,
                "primary_understanding_call_count": 0,
                "full_context_call_count": 0,
                "model_reparse_count": 0,
                "structured_followup_call_count": 0,
                "artifact_reuse_count": 0,
                "semantic_summary_cache_hit_count": 0,
                "escalation_count": 0,
                "model_call_kinds": {},
            },
        )

    def append_event(
        self,
        *,
        phase: str,
        event_type: str,
        status: str = "ok",
        actor: str,
        data: dict[str, Any] | None = None,
        goal_id: str | None = None,
        turn_id: str | None = None,
        attempt_id: str | None = None,
        plan_id: str | None = None,
        review_id: str | None = None,
        step_id: str | None = None,
        selected_strategy_id: str | None = None,
    ) -> dict[str, Any]:
        self._event_count += 1
        entry = {
            "ts": utc_now_iso(),
            "event_id": f"evt_{uuid4().hex[:12]}",
            "run_id": self.run_id,
            "goal_id": goal_id,
            "turn_id": turn_id,
            "attempt_id": attempt_id,
            "plan_id": plan_id,
            "review_id": review_id,
            "step_id": step_id,
            "phase": phase,
            "event_type": event_type,
            "status": status,
            "actor": actor,
            "selected_strategy_id": selected_strategy_id,
            "data": _trace_payload(data or {}, allow_content=self.debug),
        }
        self._append_jsonl(self.run_dir / "events.jsonl", entry)
        return entry

    def append_model_io(
        self,
        *,
        phase: str,
        provider: str,
        model: str,
        request_payload: Any = None,
        response_payload: Any = None,
        normalized_output: Any = None,
        parse_valid: bool = True,
        fallback_used: bool = False,
        error: str | None = None,
        actor: str = "provider",
        call_kind: str | None = None,
        consumed_artifacts: dict[str, Any] | None = None,
        cache_hit: bool = False,
        escalation: bool = False,
    ) -> dict[str, Any]:
        self._model_io_count += 1
        if call_kind is not None:
            self._model_call_kinds[call_kind] = self._model_call_kinds.get(call_kind, 0) + 1
        if call_kind == "full_context_understanding":
            if self._primary_understanding_call_count > 0:
                self._model_reparse_count += 1
            self._primary_understanding_call_count += 1
            self._full_context_call_count += 1
        elif call_kind == "structured_followup":
            self._structured_followup_call_count += 1
        elif call_kind is not None and call_kind.startswith("full_context"):
            self._full_context_call_count += 1
        if _references_reused_artifacts(consumed_artifacts):
            self._artifact_reuse_count += 1
        if cache_hit:
            self._semantic_summary_cache_hit_count += 1
        if escalation:
            self._escalation_count += 1
        request_artifact = self._write_artifact("request", provider, _trace_payload(request_payload, allow_content=True)) if self.debug else None
        response_artifact = self._write_artifact("response", provider, _trace_payload(response_payload, allow_content=True)) if self.debug else None
        entry = {
            "ts": utc_now_iso(),
            "record_id": f"mdl_{uuid4().hex[:12]}",
            "run_id": self.run_id,
            "phase": phase,
            "provider": provider,
            "model": model,
            "actor": actor,
            "parse_valid": parse_valid,
            "fallback_used": fallback_used,
            "error": error,
            "request_artifact": request_artifact,
            "response_artifact": response_artifact,
            "call_kind": call_kind,
            "consumed_artifacts": _trace_payload(consumed_artifacts or {}, allow_content=self.debug),
            "cache_hit": cache_hit,
            "escalation": escalation,
            "normalized_output": _trace_payload(normalized_output, allow_content=self.debug),
        }
        self._append_jsonl(self.run_dir / "model_io.jsonl", entry)
        return entry

    def finalize(
        self,
        *,
        status: str,
        goal_id: str | None = None,
        review_id: str | None = None,
        message: str = "",
        overall_status: str | None = None,
        selected_strategy_id: str | None = None,
        selected_target: str | None = None,
        plan_id: str | None = None,
    ) -> None:
        ended_at = utc_now_iso()
        summary = {
            "trace_version": self.trace_version,
            "run_id": self.run_id,
            "goal_id": goal_id,
            "review_id": review_id,
            "plan_id": plan_id,
            "status": status,
            "overall_status": overall_status or status,
            "message": message,
            "selected_strategy_id": selected_strategy_id,
            "selected_target": selected_target,
            "started_at": self.started_at,
            "ended_at": ended_at,
            "event_count": self._event_count,
            "model_io_count": self._model_io_count,
            "primary_understanding_call_count": self._primary_understanding_call_count,
            "full_context_call_count": self._full_context_call_count,
            "model_reparse_count": self._model_reparse_count,
            "structured_followup_call_count": self._structured_followup_call_count,
            "artifact_reuse_count": self._artifact_reuse_count,
            "semantic_summary_cache_hit_count": self._semantic_summary_cache_hit_count,
            "escalation_count": self._escalation_count,
            "model_call_kinds": dict(self._model_call_kinds),
        }
        self._write_json(self.run_dir / "summary.json", summary)
        manifest = self._read_json(self.run_dir / "manifest.json")
        manifest.update(
            {
                "goal_id": goal_id,
                "review_id": review_id or manifest.get("review_id"),
                "plan_id": plan_id,
                "status": status,
                "overall_status": overall_status or status,
                "message": message,
                "selected_strategy_id": selected_strategy_id,
                "selected_target": selected_target,
                "ended_at": ended_at,
                "primary_understanding_call_count": self._primary_understanding_call_count,
                "full_context_call_count": self._full_context_call_count,
                "model_reparse_count": self._model_reparse_count,
                "structured_followup_call_count": self._structured_followup_call_count,
                "artifact_reuse_count": self._artifact_reuse_count,
                "semantic_summary_cache_hit_count": self._semantic_summary_cache_hit_count,
                "escalation_count": self._escalation_count,
                "model_call_kinds": dict(self._model_call_kinds),
            }
        )
        self._write_json(self.run_dir / "manifest.json", manifest)

    def _write_artifact(self, artifact_kind: str, provider: str, payload: Any) -> str | None:
        if payload is None or payload == "":
            return None
        name = f"{artifact_kind}-{_safe_name(provider, fallback='provider')}-{uuid4().hex[:10]}.json"
        path = self.artifacts_dir / name
        self._write_json(path, _jsonable(payload))
        return str(path.relative_to(self.run_dir))

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _write_json(self, path: Path, payload: Any) -> None:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}


class TaskTraceStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or default_trace_root()

    def start_run(
        self,
        *,
        run_id: str,
        command_name: str,
        utterance: str,
        mode: str,
        transport: str | None,
        dry_run: bool,
        debug: bool,
        review_id: str | None = None,
    ) -> TaskTraceSession:
        return TaskTraceSession(
            root=self.root,
            run_id=run_id,
            command_name=command_name,
            utterance=utterance,
            mode=mode,
            transport=transport,
            dry_run=dry_run,
            debug=debug,
            review_id=review_id,
        )

    def latest_runs(self, count: int = 10) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        if not self.root.exists():
            return summaries
        for path in self.root.rglob("summary.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            payload["run_dir"] = str(path.parent)
            summaries.append(payload)
        summaries.sort(key=lambda item: str(item.get("started_at", "")), reverse=True)
        return summaries[:count]

    def summary(self, run_id: str) -> dict[str, Any] | None:
        run_dir = self._find_run_dir(run_id)
        if run_dir is None:
            return None
        return self._read_json(run_dir / "summary.json")

    def manifest(self, run_id: str) -> dict[str, Any] | None:
        run_dir = self._find_run_dir(run_id)
        if run_dir is None:
            return None
        return self._read_json(run_dir / "manifest.json")

    def events(self, run_id: str) -> list[dict[str, Any]]:
        run_dir = self._find_run_dir(run_id)
        if run_dir is None:
            return []
        return self._read_jsonl(run_dir / "events.jsonl")

    def model_io(self, run_id: str) -> list[dict[str, Any]]:
        run_dir = self._find_run_dir(run_id)
        if run_dir is None:
            return []
        return self._read_jsonl(run_dir / "model_io.jsonl")

    def _find_run_dir(self, run_id: str) -> Path | None:
        if not self.root.exists():
            return None
        for day_dir in self.root.iterdir():
            candidate = day_dir / run_id
            if day_dir.is_dir() and candidate.is_dir():
                return candidate
        for path in self.root.rglob(run_id):
            if path.is_dir():
                return path
        return None

    def _read_json(self, path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        records: list[dict[str, Any]] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records


_CURRENT_TRACE_SESSION: ContextVar[TaskTraceSession | None] = ContextVar("vibeos_trace_session", default=None)


@contextmanager
def bind_trace_session(session: TaskTraceSession) -> Iterator[TaskTraceSession]:
    token = _CURRENT_TRACE_SESSION.set(session)
    try:
        yield session
    finally:
        _CURRENT_TRACE_SESSION.reset(token)


def current_trace_session() -> TaskTraceSession | None:
    return _CURRENT_TRACE_SESSION.get()


def record_trace_event(
    *,
    phase: str,
    event_type: str,
    status: str = "ok",
    actor: str,
    data: dict[str, Any] | None = None,
    goal_id: str | None = None,
    turn_id: str | None = None,
    attempt_id: str | None = None,
    plan_id: str | None = None,
    review_id: str | None = None,
    step_id: str | None = None,
    selected_strategy_id: str | None = None,
) -> dict[str, Any] | None:
    session = current_trace_session()
    if session is None:
        return None
    return session.append_event(
        phase=phase,
        event_type=event_type,
        status=status,
        actor=actor,
        data=data,
        goal_id=goal_id,
        turn_id=turn_id,
        attempt_id=attempt_id,
        plan_id=plan_id,
        review_id=review_id,
        step_id=step_id,
        selected_strategy_id=selected_strategy_id,
    )


def record_model_io(
    *,
    phase: str,
    provider: str,
    model: str,
    request_payload: Any = None,
    response_payload: Any = None,
    normalized_output: Any = None,
    parse_valid: bool = True,
    fallback_used: bool = False,
    error: str | None = None,
    actor: str = "provider",
    call_kind: str | None = None,
    consumed_artifacts: dict[str, Any] | None = None,
    cache_hit: bool = False,
    escalation: bool = False,
) -> dict[str, Any] | None:
    session = current_trace_session()
    if session is None:
        return None
    return session.append_model_io(
        phase=phase,
        provider=provider,
        model=model,
        request_payload=request_payload,
        response_payload=response_payload,
        normalized_output=normalized_output,
        parse_valid=parse_valid,
        fallback_used=fallback_used,
        error=error,
        actor=actor,
        call_kind=call_kind,
        consumed_artifacts=consumed_artifacts,
        cache_hit=cache_hit,
        escalation=escalation,
    )
