from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib import error, request

from .assistant import ManifestProposalRequest, propose_tasks
from .board import BoardError, board_init, board_replay, board_run, board_status
from .openai_compat import OpenAICompatClient
from .orchestrator import reconcile_manifest, run_scheduler
from .providers import list_provider_profiles

DEFAULT_URL = "http://127.0.0.1:8787"


def env_value(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def agent_url(cli_value: str | None) -> str:
    return (cli_value or env_value("REMOTE_SANDBOX_URL", DEFAULT_URL)).rstrip("/")


def agent_token(cli_value: str | None) -> str:
    token = cli_value or env_value("REMOTE_SANDBOX_TOKEN")
    if not token:
        raise SystemExit("Missing token. Use --token or set REMOTE_SANDBOX_TOKEN.")
    return token


def api_request(base_url: str, token: str, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    data = None
    headers = {"Authorization": f"Bearer {token}"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(f"{base_url}{path}", method=method.upper(), headers=headers, data=data)
    try:
        with request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise SystemExit(f"Connection failed: {exc}") from exc
    return json.loads(body) if body else {}


def print_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def watch_job(base_url: str, token: str, job_id: str, poll_interval: float, shutdown_after: int | None = None) -> int:
    while True:
        job = api_request(base_url, token, "GET", f"/jobs/{job_id}")
        status = job["status"]
        print(f"[{job_id}] status={status} return_code={job.get('return_code')} pid={job.get('pid')}")
        if status in {"succeeded", "failed", "cancelled", "timeout"}:
            logs = api_request(base_url, token, "GET", f"/jobs/{job_id}/logs?stream=stdout")
            if logs.get("content"):
                print("----- stdout tail -----")
                print(logs["content"])
            if status == "succeeded" and shutdown_after is not None:
                arm = api_request(base_url, token, "POST", "/shutdown/arm", {"delay_seconds": shutdown_after})
                print(f"Auto-shutdown armed: {arm}")
            return 0 if status == "succeeded" else 1
        time.sleep(poll_interval)


def cmd_health(args) -> int:
    print_json(api_request(agent_url(args.url), agent_token(args.token), "GET", "/health"))
    return 0


def cmd_status(args) -> int:
    print_json(api_request(agent_url(args.url), agent_token(args.token), "GET", "/status"))
    return 0


def cmd_run(args) -> int:
    payload = {
        "command": args.command,
        "cwd": args.cwd,
        "timeout_seconds": args.timeout,
    }
    if args.env:
        env = {}
        for item in args.env:
            key, _, value = item.partition("=")
            env[key] = value
        payload["env"] = env
    data = api_request(agent_url(args.url), agent_token(args.token), "POST", "/jobs/run", payload)
    print_json(data)
    if args.watch:
        return watch_job(agent_url(args.url), agent_token(args.token), data["job_id"], args.poll_interval, args.shutdown_after)
    return 0


def cmd_watch(args) -> int:
    return watch_job(agent_url(args.url), agent_token(args.token), args.job_id, args.poll_interval, args.shutdown_after)


def cmd_logs(args) -> int:
    data = api_request(
        agent_url(args.url),
        agent_token(args.token),
        "GET",
        f"/jobs/{args.job_id}/logs?stream={args.stream}",
    )
    if args.raw:
        print(data.get("content", ""))
    else:
        print_json(data)
    return 0


def cmd_cancel(args) -> int:
    print_json(api_request(agent_url(args.url), agent_token(args.token), "POST", f"/jobs/{args.job_id}/cancel"))
    return 0


def cmd_lease_start(args) -> int:
    print_json(api_request(agent_url(args.url), agent_token(args.token), "POST", "/lease/start"))
    return 0


def cmd_lease_stop(args) -> int:
    print_json(api_request(agent_url(args.url), agent_token(args.token), "POST", "/lease/stop"))
    return 0


def cmd_arm_shutdown(args) -> int:
    print_json(
        api_request(
            agent_url(args.url),
            agent_token(args.token),
            "POST",
            "/shutdown/arm",
            {"delay_seconds": args.delay_seconds},
        )
    )
    return 0


def cmd_disarm_shutdown(args) -> int:
    print_json(api_request(agent_url(args.url), agent_token(args.token), "POST", "/shutdown/disarm"))
    return 0


def cmd_shutdown_now(args) -> int:
    print_json(
        api_request(
            agent_url(args.url),
            agent_token(args.token),
            "POST",
            "/shutdown/now",
            {"confirm": "shutdown-now"},
        )
    )
    return 0


def cmd_profiles(_args) -> int:
    payload = [
        {
            "name": profile.name,
            "display_name": profile.display_name,
            "description": profile.description,
            "default_hourly_rate": profile.default_hourly_rate,
            "default_shutdown_command": profile.default_shutdown_command,
        }
        for profile in list_provider_profiles()
    ]
    print_json(payload)
    return 0


def cmd_manifest_reconcile(args) -> int:
    changed, summary = reconcile_manifest(args.manifest, override_failed=args.override_failed)
    print(f"changed={changed}")
    for line in summary:
        print(line)
    return 0


def cmd_manifest_schedule(args) -> int:
    return run_scheduler(
        manifest_path=args.manifest,
        queue_log=args.queue_log,
        runs_dir=args.runs_dir,
        poll_seconds=args.poll_seconds,
        max_idle_polls=args.max_idle_polls,
    )


def cmd_assistant_propose(args) -> int:
    client = OpenAICompatClient(
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
        timeout_seconds=args.timeout_seconds,
    )
    req = ManifestProposalRequest(
        goal=args.goal,
        constraints=args.constraint or [],
        manifest_path=Path(os.path.abspath(args.manifest)) if args.manifest else None,
        max_new_tasks=args.max_new_tasks,
    )
    print_json(propose_tasks(client, req))
    return 0


def cmd_board_run(args) -> int:
    try:
        code, run_dir = board_run(
            manifest_path=Path(os.path.abspath(args.manifest)),
            run_dir=Path(os.path.abspath(args.run_dir)) if args.run_dir else None,
            base_url=args.base_url,
            api_key=args.api_key,
            model=args.model,
            timeout_seconds=args.timeout_seconds,
            resume=not args.no_resume,
        )
    except BoardError as exc:
        raise SystemExit(str(exc)) from exc
    print_json({"ok": code == 0, "run_dir": str(run_dir), "manifest": str(Path(os.path.abspath(args.manifest))), "code": code})
    return code


def cmd_board_status(args) -> int:
    try:
        print_json(board_status(Path(os.path.abspath(args.run_dir))))
    except BoardError as exc:
        raise SystemExit(str(exc)) from exc
    return 0


def cmd_board_replay(args) -> int:
    try:
        print_json(board_replay(Path(os.path.abspath(args.run_dir))))
    except BoardError as exc:
        raise SystemExit(str(exc)) from exc
    return 0


def cmd_board_init(args) -> int:
    try:
        dest = board_init(args.preset, Path(os.path.abspath(args.dest)))
    except BoardError as exc:
        raise SystemExit(str(exc)) from exc
    print_json({"preset": args.preset, "dest": str(dest)})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CLI for Remote Sandbox Framework")
    parser.add_argument("--url", default=None, help=f"Agent base URL. Default env REMOTE_SANDBOX_URL or {DEFAULT_URL}")
    parser.add_argument("--token", default=None, help="Bearer token. Default env REMOTE_SANDBOX_TOKEN")
    sub = parser.add_subparsers(dest="command_name", required=True)

    p = sub.add_parser("health")
    p.set_defaults(func=cmd_health)

    p = sub.add_parser("status")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("run")
    p.add_argument("--command", required=True)
    p.add_argument("--cwd", required=True)
    p.add_argument("--timeout", type=int, default=None)
    p.add_argument("--env", action="append", default=[], help="KEY=VALUE")
    p.add_argument("--watch", action="store_true")
    p.add_argument("--poll-interval", type=float, default=10.0)
    p.add_argument("--shutdown-after", type=int, default=None, help="Arm shutdown after successful completion")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("watch")
    p.add_argument("job_id")
    p.add_argument("--poll-interval", type=float, default=10.0)
    p.add_argument("--shutdown-after", type=int, default=None)
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("logs")
    p.add_argument("job_id")
    p.add_argument("--stream", choices=["stdout", "stderr"], default="stdout")
    p.add_argument("--raw", action="store_true")
    p.set_defaults(func=cmd_logs)

    p = sub.add_parser("cancel")
    p.add_argument("job_id")
    p.set_defaults(func=cmd_cancel)

    p = sub.add_parser("lease-start")
    p.set_defaults(func=cmd_lease_start)

    p = sub.add_parser("lease-stop")
    p.set_defaults(func=cmd_lease_stop)

    p = sub.add_parser("arm-shutdown")
    p.add_argument("delay_seconds", type=int)
    p.set_defaults(func=cmd_arm_shutdown)

    p = sub.add_parser("disarm-shutdown")
    p.set_defaults(func=cmd_disarm_shutdown)

    p = sub.add_parser("shutdown-now")
    p.set_defaults(func=cmd_shutdown_now)

    p = sub.add_parser("profiles")
    p.set_defaults(func=cmd_profiles)

    p = sub.add_parser("manifest-reconcile", help="Recompute task statuses from completion artifacts.")
    p.add_argument("--manifest", required=True, type=str)
    p.add_argument("--override-failed", action="store_true")
    p.set_defaults(
        func=lambda args: cmd_manifest_reconcile(
            argparse.Namespace(
                manifest=Path(os.path.abspath(args.manifest)),
                override_failed=args.override_failed,
            )
        )
    )

    p = sub.add_parser("manifest-schedule", help="Run the resource-aware manifest scheduler.")
    p.add_argument("--manifest", required=True, type=str)
    p.add_argument("--queue-log", required=True, type=str)
    p.add_argument("--runs-dir", required=True, type=str)
    p.add_argument("--poll-seconds", type=float, default=10.0)
    p.add_argument("--max-idle-polls", type=int, default=0)
    p.set_defaults(
        func=lambda args: cmd_manifest_schedule(
            argparse.Namespace(
                manifest=Path(os.path.abspath(args.manifest)),
                queue_log=Path(os.path.abspath(args.queue_log)),
                runs_dir=Path(os.path.abspath(args.runs_dir)),
                poll_seconds=args.poll_seconds,
                max_idle_polls=args.max_idle_polls,
            )
        )
    )

    p = sub.add_parser("assistant-propose", help="Ask an OpenAI-compatible assistant to propose manifest tasks.")
    p.add_argument("--base-url", required=True)
    p.add_argument("--api-key", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--goal", required=True)
    p.add_argument("--manifest", default=None)
    p.add_argument("--constraint", action="append", default=[])
    p.add_argument("--max-new-tasks", type=int, default=5)
    p.add_argument("--timeout-seconds", type=int, default=60)
    p.set_defaults(func=cmd_assistant_propose)

    p = sub.add_parser("board-run", help="Run a board manifest with deterministic artifact logging.")
    p.add_argument("--manifest", required=True, type=str)
    p.add_argument("--run-dir", default=None, type=str)
    p.add_argument("--base-url", default=None)
    p.add_argument("--api-key", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--timeout-seconds", type=int, default=60)
    p.add_argument("--no-resume", action="store_true")
    p.set_defaults(func=cmd_board_run)

    p = sub.add_parser("board-status", help="Read the latest board state.json from a run directory.")
    p.add_argument("--run-dir", required=True, type=str)
    p.set_defaults(func=cmd_board_status)

    p = sub.add_parser("board-replay", help="Replay board events.ndjson into a compact visible state.")
    p.add_argument("--run-dir", required=True, type=str)
    p.set_defaults(func=cmd_board_replay)

    p = sub.add_parser("board-init", help="Copy a board preset into a destination directory.")
    p.add_argument("--preset", required=True)
    p.add_argument("--dest", required=True)
    p.set_defaults(func=cmd_board_init)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
