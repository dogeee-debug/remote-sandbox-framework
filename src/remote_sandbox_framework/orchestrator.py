from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO


@dataclass
class LaunchSpec:
    popen_args: str | list[str]
    shell: bool
    cwd: str | None
    env: dict[str, str] | None


@dataclass
class RunningStage:
    task_name: str
    stage_name: str
    slot_kind: str
    slot_id: int
    started_at: float
    process: subprocess.Popen[str]
    log_path: Path
    log_handle: TextIO


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{now_iso()}] {message}\n")


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(path: Path, payload: dict[str, Any]) -> None:
    write_json(path, payload)


def _merge_dicts(*parts: dict[str, Any] | None) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for part in parts:
        if not part:
            continue
        merged.update(part)
    return merged


def resolve_runner(
    manifest: dict[str, Any],
    task: dict[str, Any],
    stage: dict[str, Any],
) -> dict[str, Any]:
    profiles = manifest.get("runner_profiles") or {}
    task_runner = task.get("runner") or {}
    stage_runner = stage.get("runner") or {}
    profile_name = stage_runner.get("profile") or task_runner.get("profile")
    profile = profiles.get(profile_name, {}) if profile_name else {}
    runner = _merge_dicts(profile, task_runner, stage_runner)
    runner.setdefault("type", "local_shell")
    return runner


def _remote_test_file_exists(runner: dict[str, Any], target: str) -> bool:
    cmd = build_ssh_command(runner, f"test -e {shlex.quote(target)}")
    result = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=int(runner.get("check_timeout_seconds", 30)),
    )
    return result.returncode == 0


def is_completion_satisfied(check: dict[str, Any] | None, runner: dict[str, Any] | None = None) -> bool:
    if not check:
        return False
    if check.get("kind") != "file_exists":
        return False
    target = str(check.get("path") or "").strip()
    if not target:
        return False
    runner = runner or {"type": "local_shell"}
    if runner.get("type") == "ssh_shell":
        return _remote_test_file_exists(runner, target)
    return Path(target).exists()


def smoke_ok(manifest: dict[str, Any], task: dict[str, Any]) -> bool:
    smoke = task.get("smoke")
    if not smoke:
        return True
    first_stage = (task.get("stages") or [{}])[0]
    runner = resolve_runner(manifest, task, first_stage)
    return is_completion_satisfied(smoke.get("completion"), runner)


def stage_complete(manifest: dict[str, Any], task: dict[str, Any], stage: dict[str, Any]) -> bool:
    runner = resolve_runner(manifest, task, stage)
    return is_completion_satisfied(stage.get("completion"), runner)


def task_done(manifest: dict[str, Any], task: dict[str, Any]) -> bool:
    stages = task.get("stages") or []
    return bool(stages) and all(stage_complete(manifest, task, stage) for stage in stages)


def stage_log_path(runs_dir: Path, task_name: str, stage_name: str) -> Path:
    safe_task = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in task_name)
    safe_stage = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in stage_name)
    return runs_dir / f"{safe_task}--{safe_stage}.log"


def build_ssh_command(runner: dict[str, Any], remote_command: str) -> list[str]:
    host = str(runner.get("host") or "").strip()
    if not host:
        raise ValueError("ssh_shell runner requires 'host'")
    user = str(runner.get("user") or "").strip()
    port = runner.get("port")
    identity_file = str(runner.get("identity_file") or "").strip()
    extra_args = [str(item) for item in runner.get("ssh_args") or []]

    ssh_cmd = ["ssh"]
    if port:
        ssh_cmd += ["-p", str(port)]
    if identity_file:
        ssh_cmd += ["-i", identity_file]
    ssh_cmd += extra_args
    ssh_cmd.append(f"{user}@{host}" if user else host)
    ssh_cmd.append(remote_command)
    return ssh_cmd


def build_launch_spec(
    manifest: dict[str, Any],
    task: dict[str, Any],
    stage: dict[str, Any],
    slot_kind: str,
    slot_id: int,
) -> LaunchSpec:
    runner = resolve_runner(manifest, task, stage)
    env = os.environ.copy()
    env.update({str(k): str(v) for k, v in (task.get("env") or {}).items()})
    env.update({str(k): str(v) for k, v in (stage.get("env") or {}).items()})
    env["GCOT_SCHED_SLOT_KIND"] = slot_kind
    env["GCOT_SCHED_SLOT_ID"] = str(slot_id)
    command = str(stage["command"])

    if runner.get("type") == "local_shell":
        return LaunchSpec(
            popen_args=command,
            shell=True,
            cwd=stage.get("workdir") or task.get("workdir"),
            env=env,
        )

    if runner.get("type") == "ssh_shell":
        remote_workdir = stage.get("workdir") or task.get("workdir") or runner.get("workdir")
        export_parts = [
            f"export {shlex.quote(key)}={shlex.quote(str(value))}"
            for key, value in env.items()
            if key.startswith("GCOT_SCHED_") or key in (stage.get("forward_env") or task.get("forward_env") or [])
        ]
        remote_parts: list[str] = []
        if remote_workdir:
            remote_parts.append(f"cd {shlex.quote(str(remote_workdir))}")
        remote_parts.extend(export_parts)
        remote_parts.append(command)
        remote_command = " && ".join(part for part in remote_parts if part)
        return LaunchSpec(
            popen_args=build_ssh_command(runner, remote_command),
            shell=False,
            cwd=None,
            env=None,
        )

    raise ValueError(f"Unsupported runner type: {runner.get('type')}")


def launch_stage(
    manifest: dict[str, Any],
    task: dict[str, Any],
    stage: dict[str, Any],
    slot_kind: str,
    slot_id: int,
    runs_dir: Path,
    queue_log: Path,
) -> RunningStage:
    spec = build_launch_spec(manifest, task, stage, slot_kind, slot_id)
    log_path = stage_log_path(runs_dir, task_name=task["name"], stage_name=stage["name"])
    append_log(
        queue_log,
        f"stage_start task={task['name']} stage={stage['name']} slot={slot_kind}:{slot_id} "
        f"log={log_path} cmd={stage['command']}",
    )
    log_handle = log_path.open("a", encoding="utf-8")
    log_handle.write(f"[{now_iso()}] command={stage['command']}\n")
    log_handle.flush()
    process = subprocess.Popen(
        spec.popen_args,
        cwd=spec.cwd,
        env=spec.env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        shell=spec.shell,
        text=True,
    )
    return RunningStage(
        task_name=task["name"],
        stage_name=stage["name"],
        slot_kind=slot_kind,
        slot_id=slot_id,
        started_at=time.time(),
        process=process,
        log_path=log_path,
        log_handle=log_handle,
    )


def reconcile_manifest(manifest_path: Path, override_failed: bool = False) -> tuple[int, list[str]]:
    manifest = load_manifest(manifest_path)
    tasks = manifest.get("tasks") or []
    changed = 0
    summary: list[str] = []
    for task in tasks:
        current = str(task.get("status", "queued"))
        if current == "failed" and not override_failed:
            summary.append(f"{task.get('name', '<unnamed>')}: failed (kept)")
            continue
        if task_done(manifest, task):
            desired = "done"
        elif not smoke_ok(manifest, task):
            desired = "waiting_smoke"
        else:
            desired = "queued"
        if desired != current:
            task["status"] = desired
            changed += 1
        summary.append(f"{task.get('name', '<unnamed>')}: {current} -> {desired}")
    if changed:
        save_manifest(manifest_path, manifest)
    return changed, summary


def run_scheduler(
    manifest_path: Path,
    queue_log: Path,
    runs_dir: Path,
    poll_seconds: float = 10.0,
    max_idle_polls: int = 0,
) -> int:
    manifest = load_manifest(manifest_path)
    slots = manifest.get("resource_slots") or {"cpu": 1, "gpu": 1}
    runs_dir.mkdir(parents=True, exist_ok=True)
    running: list[RunningStage] = []
    idle_polls = 0
    append_log(queue_log, f"scheduler_started manifest={manifest_path}")

    while True:
        manifest = load_manifest(manifest_path)
        tasks = sorted(
            manifest.get("tasks") or [],
            key=lambda item: (int(item.get("priority", 9999)), str(item.get("name", ""))),
        )
        progress = False

        still_running: list[RunningStage] = []
        for job in running:
            rc = job.process.poll()
            if rc is None:
                still_running.append(job)
                continue
            job.log_handle.close()
            append_log(
                queue_log,
                f"stage_exit task={job.task_name} stage={job.stage_name} "
                f"slot={job.slot_kind}:{job.slot_id} rc={rc} log={job.log_path}",
            )
            progress = True
        running = still_running

        used_slots: dict[str, set[int]] = {"cpu": set(), "gpu": set()}
        for job in running:
            used_slots.setdefault(job.slot_kind, set()).add(job.slot_id)

        for task in tasks:
            if task.get("status") in {"done", "failed"}:
                continue

            if task_done(manifest, task):
                task["status"] = "done"
                append_log(queue_log, f"task_done task={task['name']}")
                progress = True
                continue

            if not smoke_ok(manifest, task):
                if task.get("status") != "waiting_smoke":
                    task["status"] = "waiting_smoke"
                    append_log(queue_log, f"task_wait_smoke task={task['name']}")
                    progress = True
                continue

            active_stage_names = {job.stage_name for job in running if job.task_name == task["name"]}
            if active_stage_names:
                task["status"] = "running"
                continue

            next_stage = None
            for stage in task.get("stages") or []:
                if stage_complete(manifest, task, stage):
                    continue
                next_stage = stage
                break

            if next_stage is None:
                task["status"] = "done"
                progress = True
                continue

            slot_kind = str(next_stage.get("slot", "cpu"))
            total_slots = int(slots.get(slot_kind, 0))
            available_slot = None
            for slot_id in range(total_slots):
                if slot_id not in used_slots.setdefault(slot_kind, set()):
                    available_slot = slot_id
                    break

            if available_slot is None:
                if task.get("status") != "queued":
                    task["status"] = "queued"
                    progress = True
                continue

            job = launch_stage(
                manifest=manifest,
                task=task,
                stage=next_stage,
                slot_kind=slot_kind,
                slot_id=available_slot,
                runs_dir=runs_dir,
                queue_log=queue_log,
            )
            running.append(job)
            used_slots[slot_kind].add(available_slot)
            task["status"] = "running"
            progress = True

        save_manifest(manifest_path, manifest)

        if all(task.get("status") == "done" for task in tasks) and not running:
            append_log(queue_log, "scheduler_finished status=done")
            return 0

        if running or progress:
            idle_polls = 0
        else:
            pending = [task["name"] for task in tasks if task.get("status") != "done"]
            idle_polls += 1
            append_log(queue_log, f"scheduler_idle poll={idle_polls} pending={pending}")
            if max_idle_polls > 0 and idle_polls >= max_idle_polls:
                append_log(queue_log, f"scheduler_stalled pending={pending}")
                return 1

        time.sleep(poll_seconds)
