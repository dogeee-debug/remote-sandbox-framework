from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .providers import ProviderProfile, resolve_provider


def _bool_env(name: str, default: bool = False) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class RemoteSandboxConfig:
    token: str
    workspace_root: Path
    runtime_root: Path
    host: str
    port: int
    provider: ProviderProfile
    hourly_rate: float
    autostart_lease: bool
    idle_shutdown_seconds: int
    shutdown_enabled: bool
    shutdown_command: str
    log_tail_lines: int
    command_default_timeout: int
    env_prefix: str = "REMOTE_SANDBOX"

    @classmethod
    def from_env(cls, env_prefix: str = "REMOTE_SANDBOX") -> "RemoteSandboxConfig":
        project_root = Path(__file__).resolve().parents[2]
        provider = resolve_provider(os.getenv(f"{env_prefix}_PROVIDER", "generic"))
        runtime_root = Path(os.getenv(f"{env_prefix}_RUNTIME_ROOT", project_root / "runtime")).expanduser().resolve()
        runtime_root.mkdir(parents=True, exist_ok=True)
        token = os.getenv(f"{env_prefix}_TOKEN", "").strip()
        if not token:
            raise RuntimeError(f"{env_prefix}_TOKEN is required.")
        workspace_root = Path(os.getenv(f"{env_prefix}_WORKSPACE", project_root)).expanduser().resolve()
        return cls(
            token=token,
            workspace_root=workspace_root,
            runtime_root=runtime_root,
            host=os.getenv(f"{env_prefix}_HOST", "0.0.0.0"),
            port=int(os.getenv(f"{env_prefix}_PORT", "8787")),
            provider=provider,
            hourly_rate=float(os.getenv(f"{env_prefix}_HOURLY_RATE", str(provider.default_hourly_rate))),
            autostart_lease=_bool_env(f"{env_prefix}_AUTOSTART_LEASE", True),
            idle_shutdown_seconds=int(os.getenv(f"{env_prefix}_IDLE_SHUTDOWN_SECONDS", "300")),
            shutdown_enabled=_bool_env(f"{env_prefix}_ENABLE_SHUTDOWN", False),
            shutdown_command=os.getenv(
                f"{env_prefix}_SHUTDOWN_COMMAND",
                provider.default_shutdown_command,
            ),
            log_tail_lines=int(os.getenv(f"{env_prefix}_LOG_TAIL_LINES", "200")),
            command_default_timeout=int(os.getenv(f"{env_prefix}_DEFAULT_TIMEOUT", "0")),
            env_prefix=env_prefix,
        )
