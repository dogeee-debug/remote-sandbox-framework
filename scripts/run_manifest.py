from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from remote_sandbox_framework.orchestrator import run_scheduler  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 3:
        print("usage: python scripts/run_manifest.py <manifest> <queue_log> <runs_dir> [poll_seconds] [max_idle_polls]")
        return 2
    manifest = Path(argv[0]).resolve()
    queue_log = Path(argv[1]).resolve()
    runs_dir = Path(argv[2]).resolve()
    poll_seconds = float(argv[3]) if len(argv) > 3 else 10.0
    max_idle_polls = int(argv[4]) if len(argv) > 4 else 0
    return run_scheduler(
        manifest_path=manifest,
        queue_log=queue_log,
        runs_dir=runs_dir,
        poll_seconds=poll_seconds,
        max_idle_polls=max_idle_polls,
    )


if __name__ == "__main__":
    raise SystemExit(main())
