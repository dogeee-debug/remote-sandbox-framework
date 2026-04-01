from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException

from .config import RemoteSandboxConfig
from .models import (
    JobLogResponse,
    JobResponse,
    LeaseResponse,
    ProviderResponse,
    RunArtifactResponse,
    RunCommandRequest,
    ShutdownArmRequest,
    ShutdownNowRequest,
    StatusResponse,
    TerminalSessionResponse,
)
from .state import SandboxState


def create_app() -> FastAPI:
    config = RemoteSandboxConfig.from_env()
    state = SandboxState(config)
    app = FastAPI(title="Remote Sandbox Framework", version="0.1.0")
    app.state.config = config
    app.state.sandbox_state = state

    def require_token(authorization: str | None = Header(default=None)) -> None:
        expected = f"Bearer {config.token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="Unauthorized")

    def safe_cwd(requested: str | None) -> Path:
        target = (config.workspace_root if not requested else Path(requested)).expanduser().resolve()
        if config.workspace_root not in {target, *target.parents}:
            raise HTTPException(
                status_code=400,
                detail=f"cwd must stay under workspace: {config.workspace_root}",
            )
        return target

    async def run_job_task(
        job_id: str,
        command: str,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: int | None,
    ) -> None:
        job = state.jobs[job_id]
        if job.status == "cancelled":
            return
        job.started_at = time.time()
        job.status = "running"
        stdout_fp = job.stdout_path.open("wb")
        stderr_fp = job.stderr_path.open("wb")
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=str(cwd),
                env=env,
                stdout=stdout_fp,
                stderr=stderr_fp,
                start_new_session=(os.name != "nt"),
            )
            job.process = process
            job.pid = process.pid
            wait_coro = process.wait()
            if timeout_seconds and timeout_seconds > 0:
                try:
                    await asyncio.wait_for(wait_coro, timeout=timeout_seconds)
                except asyncio.TimeoutError:
                    try:
                        if os.name != "nt":
                            os.killpg(process.pid, 15)
                        else:
                            process.terminate()
                    except ProcessLookupError:
                        pass
                    await process.wait()
                    job.status = "timeout"
                    job.return_code = process.returncode
                    return
            else:
                await wait_coro
            if job.status != "cancelled":
                job.status = "succeeded" if process.returncode == 0 else "failed"
            job.return_code = process.returncode
        finally:
            stdout_fp.close()
            stderr_fp.close()
            job.finished_at = time.time()
            job.process = None
            state.maybe_delay_shutdown_for_active_jobs()

    @app.on_event("startup")
    async def startup() -> None:
        async def shutdown_watchdog() -> None:
            while True:
                await asyncio.sleep(5)
                await state.maybe_shutdown()

        asyncio.create_task(shutdown_watchdog())

    @app.get("/health")
    async def health(_: None = Depends(require_token)) -> dict:
        return {
            "ok": True,
            "service_started_at": state.service_started_at,
            "provider": config.provider.name,
        }

    @app.get("/status", response_model=StatusResponse)
    async def status(_: None = Depends(require_token)) -> StatusResponse:
        jobs = {job_id: job.to_dict() for job_id, job in state.jobs.items()}
        return StatusResponse(
            service_started_at=state.service_started_at,
            workspace_root=str(config.workspace_root),
            runtime_root=str(config.runtime_root),
            provider=ProviderResponse(
                name=config.provider.name,
                display_name=config.provider.display_name,
                description=config.provider.description,
            ),
            active_jobs=[job.job_id for job in state.active_jobs()],
            lease=LeaseResponse(**state.lease_snapshot()),
            auto_shutdown_armed=state.auto_shutdown_armed,
            auto_shutdown_due_at=state.auto_shutdown_due_at,
            shutdown_enabled=config.shutdown_enabled,
            jobs=jobs,
            terminal_sessions=list_terminal_sessions(),
            recent_runs=list_recent_runs(config),
        )

    @app.post("/lease/start", response_model=LeaseResponse)
    async def lease_start(_: None = Depends(require_token)) -> LeaseResponse:
        return LeaseResponse(**state.start_lease())

    @app.post("/lease/stop", response_model=LeaseResponse)
    async def lease_stop(_: None = Depends(require_token)) -> LeaseResponse:
        return LeaseResponse(**state.stop_lease())

    @app.post("/jobs/run", response_model=JobResponse)
    async def run_job(request: RunCommandRequest, _: None = Depends(require_token)) -> JobResponse:
        cwd = safe_cwd(request.cwd)
        timeout_seconds = config.command_default_timeout if request.timeout_seconds is None else request.timeout_seconds
        job = state.create_job(request.command, str(cwd), timeout_seconds)
        env = os.environ.copy()
        env.update(request.env or {})
        asyncio.create_task(run_job_task(job.job_id, request.command, cwd, env, timeout_seconds))
        return JobResponse(**job.to_dict())

    @app.get("/jobs/{job_id}", response_model=JobResponse)
    async def get_job(job_id: str, _: None = Depends(require_token)) -> JobResponse:
        job = state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return JobResponse(**job.to_dict())

    @app.get("/jobs/{job_id}/logs", response_model=JobLogResponse)
    async def get_job_logs(job_id: str, stream: str = "stdout", _: None = Depends(require_token)) -> JobLogResponse:
        job = state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if stream not in {"stdout", "stderr"}:
            raise HTTPException(status_code=400, detail="stream must be stdout or stderr")
        path = job.stdout_path if stream == "stdout" else job.stderr_path
        if not path.exists():
            content = ""
        else:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            content = "\n".join(lines[-config.log_tail_lines :])
        return JobLogResponse(job_id=job_id, stream=stream, path=str(path), content=content)

    @app.post("/jobs/{job_id}/cancel", response_model=JobResponse)
    async def cancel_job(job_id: str, _: None = Depends(require_token)) -> JobResponse:
        if job_id not in state.jobs:
            raise HTTPException(status_code=404, detail="Job not found")
        job = await state.cancel_job(job_id)
        return JobResponse(**job.to_dict())

    @app.post("/shutdown/arm")
    async def arm_shutdown(request: ShutdownArmRequest, _: None = Depends(require_token)) -> dict:
        due_at = state.arm_shutdown(request.delay_seconds)
        return {"armed": True, "due_at": due_at}

    @app.post("/shutdown/disarm")
    async def disarm_shutdown(_: None = Depends(require_token)) -> dict:
        state.disarm_shutdown()
        return {"armed": False}

    @app.post("/shutdown/now")
    async def shutdown_now(request: ShutdownNowRequest, _: None = Depends(require_token)) -> dict:
        if request.confirm != "shutdown-now":
            raise HTTPException(status_code=400, detail='confirm must equal "shutdown-now"')
        state.auto_shutdown_armed = True
        state.auto_shutdown_due_at = time.time()
        await state.maybe_shutdown()
        return {"accepted": True, "shutdown_enabled": config.shutdown_enabled}

    return app


def list_terminal_sessions() -> list[TerminalSessionResponse]:
    if shutil.which("tmux") is None:
        return []
    try:
        output = subprocess.check_output(
            ["tmux", "list-sessions", "-F", "#{session_name}\t#{session_attached}\t#{session_windows}\t#{session_created}"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except subprocess.CalledProcessError:
        return []

    sessions: list[TerminalSessionResponse] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        name, attached, windows, created = (line.split("\t") + ["", "", "", ""])[:4]
        sessions.append(
            TerminalSessionResponse(
                session_name=name,
                attached=attached == "1",
                windows=int(windows) if windows.isdigit() else None,
                created_at=int(created) if created.isdigit() else None,
            )
        )
    return sessions


def list_recent_runs(config: RemoteSandboxConfig, limit: int = 10) -> list[RunArtifactResponse]:
    candidate_roots = [config.workspace_root / "runtime" / "runs", config.runtime_root / "runs"]
    run_dirs: list[Path] = []
    seen: set[Path] = set()
    for runs_root in candidate_roots:
        if not runs_root.exists():
            continue
        for path in runs_root.iterdir():
            if path.is_dir() and path not in seen:
                run_dirs.append(path)
                seen.add(path)
    run_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    results: list[RunArtifactResponse] = []
    for run_dir in run_dirs[:limit]:
        meta_path = run_dir / "meta.json"
        payload: dict[str, str] = {}
        if meta_path.exists():
            try:
                payload = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                payload = {}
        results.append(
            RunArtifactResponse(
                run_dir=str(run_dir),
                meta_path=str(meta_path),
                stdout_path=payload.get("stdout_log"),
                stderr_path=payload.get("stderr_log"),
                command_path=payload.get("command_file"),
                session_name=payload.get("session_name"),
                job_name=payload.get("job_name"),
            )
        )
    return results


def run() -> int:
    config = RemoteSandboxConfig.from_env()
    uvicorn.run(
        "remote_sandbox_framework.app:create_app",
        factory=True,
        host=config.host,
        port=config.port,
    )
    return 0
