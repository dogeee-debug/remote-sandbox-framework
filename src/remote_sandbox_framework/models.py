from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RunCommandRequest(BaseModel):
    command: str = Field(..., min_length=1)
    cwd: str | None = None
    env: dict[str, str] | None = None
    timeout_seconds: int | None = Field(default=None, ge=0)


class JobResponse(BaseModel):
    job_id: str
    status: str
    command: str
    cwd: str
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    return_code: int | None = None
    pid: int | None = None
    stdout_path: str
    stderr_path: str
    timeout_seconds: int | None = None


class JobLogResponse(BaseModel):
    job_id: str
    stream: str
    path: str
    content: str


class LeaseResponse(BaseModel):
    active: bool
    started_at: float | None
    elapsed_seconds: float
    hourly_rate: float
    estimated_cost: float


class ProviderResponse(BaseModel):
    name: str
    display_name: str
    description: str


class TerminalSessionResponse(BaseModel):
    backend: str = "tmux"
    session_name: str
    attached: bool
    windows: int | None = None
    created_at: int | None = None


class RunArtifactResponse(BaseModel):
    run_dir: str
    meta_path: str
    stdout_path: str | None = None
    stderr_path: str | None = None
    command_path: str | None = None
    session_name: str | None = None
    job_name: str | None = None


class ShutdownArmRequest(BaseModel):
    delay_seconds: int | None = Field(default=None, ge=0)


class ShutdownNowRequest(BaseModel):
    confirm: str


class StatusResponse(BaseModel):
    service_started_at: float
    workspace_root: str
    runtime_root: str
    provider: ProviderResponse
    active_jobs: list[str]
    lease: LeaseResponse
    auto_shutdown_armed: bool
    auto_shutdown_due_at: float | None
    shutdown_enabled: bool
    jobs: dict[str, dict[str, Any]]
    terminal_sessions: list[TerminalSessionResponse] = Field(default_factory=list)
    recent_runs: list[RunArtifactResponse] = Field(default_factory=list)
