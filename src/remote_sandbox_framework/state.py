from __future__ import annotations

import asyncio
import os
import signal
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .config import RemoteSandboxConfig


@dataclass
class JobRecord:
    job_id: str
    command: str
    cwd: str
    stdout_path: Path
    stderr_path: Path
    timeout_seconds: int | None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    return_code: int | None = None
    pid: int | None = None
    status: str = "queued"
    process: asyncio.subprocess.Process | None = None

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "command": self.command,
            "cwd": self.cwd,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "return_code": self.return_code,
            "pid": self.pid,
            "stdout_path": str(self.stdout_path),
            "stderr_path": str(self.stderr_path),
            "timeout_seconds": self.timeout_seconds,
        }


class SandboxState:
    def __init__(self, config: RemoteSandboxConfig):
        self.config = config
        self.service_started_at = time.time()
        self.jobs: dict[str, JobRecord] = {}
        self.lease_started_at: float | None = time.time() if config.autostart_lease else None
        self.accumulated_seconds = 0.0
        self.auto_shutdown_armed = False
        self.auto_shutdown_due_at: float | None = None
        self._shutdown_executed = False
        self.jobs_root = config.runtime_root / "jobs"
        self.jobs_root.mkdir(parents=True, exist_ok=True)

    def create_job(self, command: str, cwd: str, timeout_seconds: int | None) -> JobRecord:
        job_id = uuid.uuid4().hex[:12]
        stdout_path = self.jobs_root / f"{job_id}.stdout.log"
        stderr_path = self.jobs_root / f"{job_id}.stderr.log"
        job = JobRecord(
            job_id=job_id,
            command=command,
            cwd=cwd,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            timeout_seconds=timeout_seconds,
        )
        self.jobs[job_id] = job
        return job

    def lease_snapshot(self) -> dict:
        elapsed = self.accumulated_seconds
        if self.lease_started_at is not None:
            elapsed += max(0.0, time.time() - self.lease_started_at)
        return {
            "active": self.lease_started_at is not None,
            "started_at": self.lease_started_at,
            "elapsed_seconds": elapsed,
            "hourly_rate": self.config.hourly_rate,
            "estimated_cost": elapsed / 3600.0 * self.config.hourly_rate,
        }

    def start_lease(self) -> dict:
        if self.lease_started_at is None:
            self.lease_started_at = time.time()
        return self.lease_snapshot()

    def stop_lease(self) -> dict:
        if self.lease_started_at is not None:
            self.accumulated_seconds += max(0.0, time.time() - self.lease_started_at)
            self.lease_started_at = None
        return self.lease_snapshot()

    def active_jobs(self) -> list[JobRecord]:
        return [job for job in self.jobs.values() if job.status in {"queued", "running"}]

    def arm_shutdown(self, delay_seconds: int | None = None) -> float:
        delay = self.config.idle_shutdown_seconds if delay_seconds is None else delay_seconds
        self.auto_shutdown_armed = True
        self.auto_shutdown_due_at = time.time() + delay
        return self.auto_shutdown_due_at

    def disarm_shutdown(self) -> None:
        self.auto_shutdown_armed = False
        self.auto_shutdown_due_at = None

    def maybe_delay_shutdown_for_active_jobs(self) -> None:
        if self.auto_shutdown_armed and self.active_jobs():
            self.auto_shutdown_due_at = time.time() + self.config.idle_shutdown_seconds

    async def cancel_job(self, job_id: str) -> JobRecord:
        job = self.jobs[job_id]
        if job.status == "queued" and job.process is None:
            job.status = "cancelled"
            job.finished_at = time.time()
            return job
        if job.process is None or job.status not in {"queued", "running"}:
            return job
        if os.name != "nt":
            os.killpg(job.process.pid, signal.SIGTERM)
        else:
            job.process.terminate()
        job.status = "cancelled"
        job.finished_at = time.time()
        return job

    async def maybe_shutdown(self) -> bool:
        if self._shutdown_executed or not self.auto_shutdown_armed or self.auto_shutdown_due_at is None:
            return False
        if self.active_jobs() or time.time() < self.auto_shutdown_due_at:
            return False
        self._shutdown_executed = True
        if not self.config.shutdown_enabled:
            marker = self.config.runtime_root / "shutdown.skipped"
            marker.write_text(
                f"Would execute: {self.config.shutdown_command} @ {time.strftime('%Y-%m-%d %H:%M:%S')}\n",
                encoding="utf-8",
            )
            return True
        process = await asyncio.create_subprocess_shell(self.config.shutdown_command)
        await process.wait()
        return True
