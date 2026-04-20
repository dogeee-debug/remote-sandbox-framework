from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .assistant import ManifestProposalRequest, build_proposal_prompt
from .openai_compat import OpenAICompatClient, extract_first_text


BOARD_KIND = "board"
BOARD_VERSION = 2
BOARD_RUNS_DIRNAME = "board-runs"

TASK_MODES = {"llm_propose", "shell_stage", "review_gate", "synthesis"}


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _safe_slug(value: str) -> str:
    raw = value.strip().lower() or "run"
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in raw).strip("-") or "run"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _default_board_runs_root(manifest_path: Path) -> Path:
    return Path.cwd().resolve() / "runtime" / BOARD_RUNS_DIRNAME


def _copy_if_missing(src: Path, dest: Path) -> None:
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)


def _extract_usage(response: dict[str, Any]) -> dict[str, int] | None:
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return None
    payload: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int):
            payload[key] = value
    return payload or None


def _task_log_path(run_dir: Path, task_id: str) -> Path:
    return run_dir / "logs" / f"{task_id}.log"


@dataclass(slots=True)
class BoardArtifact:
    name: str
    path: Path
    format: str = "text"
    required: bool = False
    description: str = ""


@dataclass(slots=True)
class BoardTask:
    id: str
    role: str
    mode: str
    goal: str
    depends_on: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    produces: list[str] = field(default_factory=list)
    runner: dict[str, Any] = field(default_factory=dict)
    completion: dict[str, Any] | None = None
    prompt: str | None = None
    command: str | None = None
    review: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BoardDefinition:
    manifest_path: Path
    name: str
    description: str
    artifacts: dict[str, BoardArtifact]
    tasks: list[BoardTask]
    metadata: dict[str, Any] = field(default_factory=dict)


class BoardError(RuntimeError):
    pass


def load_board_definition(path: Path) -> BoardDefinition:
    payload = _load_json(path)
    kind = payload.get("kind")
    version = payload.get("version")
    if kind != BOARD_KIND:
        raise BoardError(f"Expected manifest kind={BOARD_KIND!r}, got {kind!r}")
    if version != BOARD_VERSION:
        raise BoardError(f"Expected board version={BOARD_VERSION}, got {version!r}")

    artifacts_payload = payload.get("artifacts") or []
    if not isinstance(artifacts_payload, list) or not artifacts_payload:
        raise BoardError("Board manifest requires a non-empty artifacts list.")

    tasks_payload = payload.get("tasks") or []
    if not isinstance(tasks_payload, list) or not tasks_payload:
        raise BoardError("Board manifest requires a non-empty tasks list.")

    artifacts: dict[str, BoardArtifact] = {}
    base_dir = path.resolve().parent
    for item in artifacts_payload:
        if not isinstance(item, dict):
            raise BoardError("Artifact entries must be JSON objects.")
        name = str(item.get("name") or "").strip()
        rel_path = str(item.get("path") or "").strip()
        if not name or not rel_path:
            raise BoardError("Artifact entries require name and path.")
        if name in artifacts:
            raise BoardError(f"Duplicate artifact name: {name}")
        artifacts[name] = BoardArtifact(
            name=name,
            path=(base_dir / rel_path).resolve(),
            format=str(item.get("format") or "text"),
            required=bool(item.get("required", False)),
            description=str(item.get("description") or ""),
        )

    tasks: list[BoardTask] = []
    seen_ids: set[str] = set()
    for item in tasks_payload:
        if not isinstance(item, dict):
            raise BoardError("Task entries must be JSON objects.")
        task_id = str(item.get("id") or "").strip()
        mode = str(item.get("mode") or "").strip()
        if not task_id:
            raise BoardError("Each board task requires an id.")
        if task_id in seen_ids:
            raise BoardError(f"Duplicate task id: {task_id}")
        if mode not in TASK_MODES:
            raise BoardError(f"Unsupported board task mode: {mode}")
        seen_ids.add(task_id)
        tasks.append(
            BoardTask(
                id=task_id,
                role=str(item.get("role") or "agent"),
                mode=mode,
                goal=str(item.get("goal") or "").strip(),
                depends_on=[str(v) for v in item.get("depends_on") or []],
                inputs=[str(v) for v in item.get("inputs") or []],
                produces=[str(v) for v in item.get("produces") or []],
                runner=dict(item.get("runner") or {}),
                completion=dict(item["completion"]) if isinstance(item.get("completion"), dict) else None,
                prompt=str(item["prompt"]) if item.get("prompt") is not None else None,
                command=str(item["command"]) if item.get("command") is not None else None,
                review=dict(item.get("review") or {}),
            )
        )

    definition = BoardDefinition(
        manifest_path=path.resolve(),
        name=str(payload.get("name") or path.stem),
        description=str(payload.get("description") or ""),
        artifacts=artifacts,
        tasks=tasks,
        metadata=dict(payload.get("metadata") or {}),
    )
    validate_board_definition(definition)
    return definition


def validate_board_definition(definition: BoardDefinition) -> None:
    task_ids = {task.id for task in definition.tasks}
    for task in definition.tasks:
        if not task.goal:
            raise BoardError(f"Task {task.id} requires a goal.")
        missing_tasks = [dep for dep in task.depends_on if dep not in task_ids]
        if missing_tasks:
            raise BoardError(f"Task {task.id} depends on unknown tasks: {missing_tasks}")
        unknown_inputs = [name for name in task.inputs if name not in definition.artifacts]
        if unknown_inputs:
            raise BoardError(f"Task {task.id} references unknown inputs: {unknown_inputs}")
        unknown_outputs = [name for name in task.produces if name not in definition.artifacts]
        if unknown_outputs:
            raise BoardError(f"Task {task.id} references unknown outputs: {unknown_outputs}")
        if task.mode in {"llm_propose", "synthesis"} and not task.produces:
            raise BoardError(f"Task {task.id} must produce at least one artifact.")
        if task.mode == "shell_stage" and not task.command:
            raise BoardError(f"Task {task.id} requires a command.")
        if task.mode == "review_gate" and not task.review:
            raise BoardError(f"Task {task.id} requires a review config.")
        if task.mode in {"llm_propose", "synthesis"} and not task.prompt:
            raise BoardError(f"Task {task.id} requires a prompt.")

    _topologically_sorted_tasks(definition.tasks)


def _topologically_sorted_tasks(tasks: list[BoardTask]) -> list[BoardTask]:
    task_map = {task.id: task for task in tasks}
    visiting: set[str] = set()
    visited: set[str] = set()
    ordered: list[BoardTask] = []

    def visit(task_id: str) -> None:
        if task_id in visited:
            return
        if task_id in visiting:
            raise BoardError(f"Board task graph contains a cycle at {task_id}")
        visiting.add(task_id)
        task = task_map[task_id]
        for dep in task.depends_on:
            visit(dep)
        visiting.remove(task_id)
        visited.add(task_id)
        ordered.append(task)

    for task in tasks:
        visit(task.id)
    return ordered


class BoardRun:
    def __init__(
        self,
        definition: BoardDefinition,
        run_dir: Path,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: int = 60,
        resume: bool = True,
    ) -> None:
        self.definition = definition
        self.run_dir = run_dir.resolve()
        self.resume = resume
        self.events_path = self.run_dir / "events.ndjson"
        self.state_path = self.run_dir / "state.json"
        self.summary_path = self.run_dir / "summary.json"
        self.logs_dir = self.run_dir / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self._can_resume_existing_state = self.resume and self.state_path.exists()
        self.client: OpenAICompatClient | None = None
        if base_url and api_key and model:
            self.client = OpenAICompatClient(
                base_url=base_url,
                api_key=api_key,
                model=model,
                timeout_seconds=timeout_seconds,
            )
        self.state = self._load_or_init_state()

    def _load_or_init_state(self) -> dict[str, Any]:
        if self._can_resume_existing_state:
            return _load_json(self.state_path)
        artifacts = {
            name: {
                "name": name,
                "path": str(artifact.path),
                "format": artifact.format,
                "required": artifact.required,
                "exists": artifact.path.exists(),
            }
            for name, artifact in self.definition.artifacts.items()
        }
        tasks = {
            task.id: {
                "id": task.id,
                "role": task.role,
                "mode": task.mode,
                "status": "pending",
                "started_at": None,
                "finished_at": None,
                "resumed": False,
                "error": None,
                "outputs": {},
            }
            for task in self.definition.tasks
        }
        state = {
            "run_dir": str(self.run_dir),
            "manifest_path": str(self.definition.manifest_path),
            "board_name": self.definition.name,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "tasks": tasks,
            "artifacts": artifacts,
        }
        self._write_state(state)
        return state

    def _write_state(self, payload: dict[str, Any] | None = None) -> None:
        state = payload or self.state
        state["updated_at"] = now_iso()
        _write_json(self.state_path, state)

    def _append_event(self, event_type: str, **payload: Any) -> None:
        event = {"ts": now_iso(), "event": event_type, **payload}
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _mark_resumed_tasks(self) -> None:
        for task in _topologically_sorted_tasks(self.definition.tasks):
            artifact_paths = [self.definition.artifacts[name].path for name in task.produces]
            if artifact_paths and all(path.exists() for path in artifact_paths):
                state_task = self.state["tasks"][task.id]
                if state_task["status"] != "completed":
                    state_task["status"] = "completed"
                    state_task["resumed"] = True
                    state_task["finished_at"] = now_iso()
                    self._append_event("task_resumed", task_id=task.id, outputs=task.produces)
        for name, artifact in self.definition.artifacts.items():
            self.state["artifacts"][name]["exists"] = artifact.path.exists()
        self._write_state()

    def run(self) -> int:
        self._append_event(
            "run_started",
            board=self.definition.name,
            manifest_path=str(self.definition.manifest_path),
            run_dir=str(self.run_dir),
        )
        if self._can_resume_existing_state:
            self._mark_resumed_tasks()
        ordered_tasks = _topologically_sorted_tasks(self.definition.tasks)
        for task in ordered_tasks:
            task_state = self.state["tasks"][task.id]
            if task_state["status"] == "completed":
                continue
            self._ensure_dependencies_completed(task)
            self._ensure_inputs_exist(task)
            task_state["status"] = "running"
            task_state["started_at"] = now_iso()
            self._append_event(
                "task_started",
                task_id=task.id,
                mode=task.mode,
                role=task.role,
                inputs=task.inputs,
                produces=task.produces,
            )
            self._append_event("task_inputs_resolved", task_id=task.id, inputs=self._collect_input_snapshot(task))
            self._write_state()
            try:
                outputs = self._execute_task(task)
            except Exception as exc:  # noqa: BLE001
                task_state["status"] = "failed"
                task_state["error"] = str(exc)
                task_state["finished_at"] = now_iso()
                self._append_event("task_failed", task_id=task.id, error=str(exc))
                self._write_state()
                self._write_summary(status="failed")
                raise
            task_state["status"] = "completed"
            task_state["finished_at"] = now_iso()
            task_state["outputs"] = outputs
            for artifact_name in task.produces:
                artifact = self.definition.artifacts[artifact_name]
                self.state["artifacts"][artifact_name]["exists"] = artifact.path.exists()
                self._append_event(
                    "artifact_written",
                    task_id=task.id,
                    artifact=artifact_name,
                    path=str(artifact.path),
                    format=artifact.format,
                )
            self._append_event("task_completed", task_id=task.id, outputs=task.produces)
            self._write_state()

        self._validate_required_artifacts()
        self._append_event("run_completed", board=self.definition.name, run_dir=str(self.run_dir))
        self._write_summary(status="completed")
        return 0

    def _validate_required_artifacts(self) -> None:
        missing = [
            name
            for name, artifact in self.definition.artifacts.items()
            if artifact.required and not artifact.path.exists()
        ]
        if missing:
            raise BoardError(f"Required artifacts missing after run: {missing}")

    def _write_summary(self, *, status: str) -> None:
        summary = {
            "board_name": self.definition.name,
            "status": status,
            "manifest_path": str(self.definition.manifest_path),
            "run_dir": str(self.run_dir),
            "tasks": list(self.state["tasks"].values()),
            "artifacts": list(self.state["artifacts"].values()),
            "updated_at": now_iso(),
        }
        _write_json(self.summary_path, summary)

    def _ensure_dependencies_completed(self, task: BoardTask) -> None:
        for dep in task.depends_on:
            state = self.state["tasks"][dep]["status"]
            if state != "completed":
                raise BoardError(f"Task {task.id} cannot run before dependency {dep} completes.")

    def _ensure_inputs_exist(self, task: BoardTask) -> None:
        missing = [name for name in task.inputs if not self.definition.artifacts[name].path.exists()]
        if missing:
            raise BoardError(f"Task {task.id} is missing required inputs: {missing}")

    def _collect_input_snapshot(self, task: BoardTask) -> list[dict[str, Any]]:
        snapshot: list[dict[str, Any]] = []
        for name in task.inputs:
            artifact = self.definition.artifacts[name]
            snapshot.append(
                {
                    "name": name,
                    "path": str(artifact.path),
                    "exists": artifact.path.exists(),
                    "format": artifact.format,
                }
            )
        return snapshot

    def _collect_inputs_text(self, task: BoardTask) -> str:
        parts: list[str] = []
        for name in task.inputs:
            artifact = self.definition.artifacts[name]
            if not artifact.path.exists():
                continue
            if artifact.format == "json":
                text = artifact.path.read_text(encoding="utf-8")
            else:
                text = artifact.path.read_text(encoding="utf-8", errors="replace")
            parts.append(f"## Artifact: {name}\nPath: {artifact.path}\n\n{text}\n")
        return "\n".join(parts).strip()

    def _render_prompt(self, task: BoardTask) -> str:
        prompt = task.prompt or ""
        input_text = self._collect_inputs_text(task)
        artifacts_text = "\n".join(
            f"- {name}: {self.definition.artifacts[name].path}"
            for name in task.produces
        )
        return (
            prompt.replace("{{goal}}", task.goal)
            .replace("{{inputs}}", input_text)
            .replace("{{produces}}", artifacts_text)
            .replace("{{board_name}}", self.definition.name)
        )

    def _execute_task(self, task: BoardTask) -> dict[str, Any]:
        if task.mode == "shell_stage":
            return self._execute_shell_task(task)
        if task.mode in {"llm_propose", "synthesis"}:
            return self._execute_llm_task(task)
        if task.mode == "review_gate":
            return self._execute_review_gate(task)
        raise BoardError(f"Unsupported task mode: {task.mode}")

    def _execute_shell_task(self, task: BoardTask) -> dict[str, Any]:
        env = os.environ.copy()
        env.update({str(k): str(v) for k, v in task.runner.get("env", {}).items()})
        env["RSF_BOARD_RUN_DIR"] = str(self.run_dir)
        env["RSF_BOARD_MANIFEST"] = str(self.definition.manifest_path)
        env["RSF_BOARD_TASK_ID"] = task.id
        env["RSF_BOARD_TASK_ROLE"] = task.role
        log_path = _task_log_path(self.run_dir, task.id)
        cwd = task.runner.get("cwd")
        if cwd:
            raw_cwd = Path(str(cwd))
            cwd_path = raw_cwd.resolve() if raw_cwd.is_absolute() else (self.definition.manifest_path.parent / raw_cwd).resolve()
        else:
            cwd_path = self.definition.manifest_path.parent.resolve()
        started = time.time()
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{now_iso()}] command={task.command}\n")
            process = subprocess.run(
                str(task.command),
                shell=True,
                cwd=str(cwd_path),
                env=env,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=int(task.runner.get("timeout_seconds", 0)) or None,
            )
        if process.returncode != 0:
            raise BoardError(f"Shell task {task.id} failed with rc={process.returncode}")
        if task.completion:
            self._validate_completion(task)
        return {
            "return_code": process.returncode,
            "log_path": str(log_path),
            "duration_seconds": round(time.time() - started, 3),
        }

    def _validate_completion(self, task: BoardTask) -> None:
        check = task.completion or {}
        if check.get("kind") != "file_exists":
            raise BoardError(f"Unsupported completion check for task {task.id}: {check}")
        raw_path = Path(str(check.get("path") or "")).expanduser()
        path = raw_path.resolve() if raw_path.is_absolute() else (self.definition.manifest_path.parent / raw_path).resolve()
        if not path.exists():
            raise BoardError(f"Completion file missing for task {task.id}: {path}")

    def _execute_llm_task(self, task: BoardTask) -> dict[str, Any]:
        if self.client is None:
            return self._execute_fallback_generation(task)
        prompt = self._render_prompt(task)
        system_prompt = (
            "You are a deterministic autoresearch worker. "
            "Return useful output only. Do not mention hidden chain-of-thought."
        )
        started = time.time()
        response = self.client.chat_completion(
            system_prompt=system_prompt,
            user_prompt=prompt,
            temperature=0.0,
            max_tokens=int(task.runner.get("max_tokens", 1600)),
        )
        text = extract_first_text(response).strip()
        if not text:
            raise BoardError(f"LLM task {task.id} returned empty content.")
        outputs = self._write_generated_outputs(task, text)
        return {
            "provider": self.client.base_url,
            "model": self.client.model,
            "latency_seconds": round(time.time() - started, 3),
            "prompt_hash": _sha256_text(prompt),
            "usage": _extract_usage(response),
            **outputs,
        }

    def _execute_fallback_generation(self, task: BoardTask) -> dict[str, Any]:
        outputs: dict[str, Any] = {
            "provider": "fallback-local",
            "model": "deterministic-template",
            "latency_seconds": 0.0,
        }
        input_snapshot = self._collect_input_snapshot(task)
        input_names = [entry["name"] for entry in input_snapshot]
        generated_json = {
            "task_id": task.id,
            "role": task.role,
            "mode": task.mode,
            "goal": task.goal,
            "inputs": input_names,
            "generated_at": now_iso(),
        }
        generated_text = (
            "# Remote Sandbox Framework\n\n"
            "## Minimal Multi-Agent Kernel\n\n"
            "Deterministic execution for artifact-first autoresearch on local or remote sandboxes.\n\n"
            "## Summary\n\n"
            f"- Task: `{task.id}`\n"
            f"- Role: `{task.role}`\n"
            f"- Mode: `{task.mode}`\n"
            f"- Inputs: {', '.join(input_names) or 'none'}\n\n"
            "## Signals\n\n"
            "- Deterministic Execution\n"
            "- Artifacts\n"
            "- Observability\n"
            "- Replayable Runs\n"
            "- Safe Shell Boundaries\n\n"
            "## Recommended Product Positioning\n\n"
            "Position this repository as a minimal multi-agent kernel, not another full-stack agent platform.\n"
            "Lead with deterministic boards, append-only events, and remote-safe execution.\n\n"
            "## Why This Is Different\n\n"
            "This fallback keeps artifact-first collaboration reproducible even without an attached LLM.\n"
            "It is intentionally smaller, more inspectable, and easier to trust than heavyweight orchestration stacks.\n"
        )
        for artifact_name in task.produces:
            artifact = self.definition.artifacts[artifact_name]
            artifact.path.parent.mkdir(parents=True, exist_ok=True)
            if artifact.format == "json":
                payload = dict(generated_json)
                payload["artifact"] = artifact_name
                artifact.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            else:
                artifact.path.write_text(
                    f"{generated_text}\nArtifact: `{artifact_name}`\n",
                    encoding="utf-8",
                )
        outputs["fallback"] = True
        return outputs

    def _write_generated_outputs(self, task: BoardTask, text: str) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        parsed_json: Any | None = None
        try:
            parsed_json = json.loads(text)
        except json.JSONDecodeError:
            parsed_json = None
        for artifact_name in task.produces:
            artifact = self.definition.artifacts[artifact_name]
            artifact.path.parent.mkdir(parents=True, exist_ok=True)
            if artifact.format == "json":
                if parsed_json is None:
                    raise BoardError(
                        f"LLM task {task.id} produced non-JSON content for JSON artifact {artifact_name}"
                    )
                artifact.path.write_text(json.dumps(parsed_json, indent=2, ensure_ascii=False), encoding="utf-8")
            else:
                artifact.path.write_text(text + ("\n" if not text.endswith("\n") else ""), encoding="utf-8")
        payload["text_length"] = len(text)
        return payload

    def _execute_review_gate(self, task: BoardTask) -> dict[str, Any]:
        rules = [str(item) for item in task.review.get("must_contain") or []]
        missing_rules: list[str] = []
        inspected: list[str] = []
        for artifact_name in task.inputs:
            artifact = self.definition.artifacts[artifact_name]
            if not artifact.path.exists():
                missing_rules.extend(rules)
                continue
            text = artifact.path.read_text(encoding="utf-8", errors="replace")
            inspected.append(artifact_name)
            for rule in rules:
                if rule not in text and rule not in missing_rules:
                    missing_rules.append(rule)
        if missing_rules:
            raise BoardError(f"Review gate {task.id} failed; missing markers: {missing_rules}")
        if task.produces:
            approved_payload = {
                "task_id": task.id,
                "approved": True,
                "checked_artifacts": inspected,
                "must_contain": rules,
                "generated_at": now_iso(),
            }
            for artifact_name in task.produces:
                artifact = self.definition.artifacts[artifact_name]
                artifact.path.parent.mkdir(parents=True, exist_ok=True)
                if artifact.format == "json":
                    artifact.path.write_text(json.dumps(approved_payload, indent=2, ensure_ascii=False), encoding="utf-8")
                else:
                    artifact.path.write_text(
                        "review gate passed\n"
                        + "\n".join(f"- checked `{name}`" for name in inspected)
                        + "\n",
                        encoding="utf-8",
                    )
        return {"approved": True, "checked_artifacts": inspected}


def board_run(
    manifest_path: Path,
    *,
    run_dir: Path | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    timeout_seconds: int = 60,
    resume: bool = True,
) -> tuple[int, Path]:
    definition = load_board_definition(manifest_path)
    root = run_dir.resolve() if run_dir else _default_board_runs_root(manifest_path) / f"{_safe_slug(definition.name)}-{time.strftime('%Y%m%d-%H%M%S')}"
    runner = BoardRun(
        definition=definition,
        run_dir=root,
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_seconds=timeout_seconds,
        resume=resume,
    )
    return runner.run(), root


def board_status(run_dir: Path) -> dict[str, Any]:
    state_path = run_dir.resolve() / "state.json"
    if not state_path.exists():
        raise BoardError(f"Missing state file: {state_path}")
    return _load_json(state_path)


def board_replay(run_dir: Path) -> dict[str, Any]:
    events_path = run_dir.resolve() / "events.ndjson"
    if not events_path.exists():
        raise BoardError(f"Missing events file: {events_path}")
    replay_state = {
        "run_dir": str(run_dir.resolve()),
        "events": 0,
        "tasks": {},
        "artifacts": {},
    }
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        replay_state["events"] += 1
        event_type = event.get("event")
        task_id = event.get("task_id")
        if event_type in {"task_started", "task_completed", "task_failed", "task_resumed"} and task_id:
            replay_state["tasks"].setdefault(task_id, {})
            replay_state["tasks"][task_id]["status"] = event_type.removeprefix("task_")
            replay_state["tasks"][task_id]["ts"] = event.get("ts")
        if event_type == "artifact_written":
            artifact_name = event.get("artifact")
            if artifact_name:
                replay_state["artifacts"][artifact_name] = {
                    "path": event.get("path"),
                    "format": event.get("format"),
                    "written_at": event.get("ts"),
                    "task_id": task_id,
                }
    return replay_state


def board_init(preset: str, dest: Path) -> Path:
    preset_key = preset.strip().lower()
    if preset_key != "repo-research":
        raise BoardError(f"Unsupported board preset: {preset}")
    skill_root = Path(__file__).resolve().parents[2]
    source_root = skill_root / "examples" / "autoresearch" / "repo-research"
    if not source_root.exists():
        raise BoardError(f"Missing preset source directory: {source_root}")
    dest_root = dest.resolve()
    dest_root.mkdir(parents=True, exist_ok=True)
    for source in source_root.rglob("*"):
        if source.is_dir():
            continue
        rel = source.relative_to(source_root)
        _copy_if_missing(source, dest_root / rel)
    return dest_root


def build_assistant_prompt_preview(goal: str, constraints: list[str], manifest_path: Path | None = None) -> str:
    req = ManifestProposalRequest(goal=goal, constraints=constraints, manifest_path=manifest_path)
    return build_proposal_prompt(req)
