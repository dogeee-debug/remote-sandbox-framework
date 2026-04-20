from __future__ import annotations

import argparse
import json
from pathlib import Path


def _section_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Create deterministic evidence JSON from repo inputs.")
    parser.add_argument("--repo-index", required=True)
    parser.add_argument("--planner-brief", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    repo_index = Path(args.repo_index).resolve().read_text(encoding="utf-8", errors="replace")
    planner_brief = Path(args.planner_brief).resolve().read_text(encoding="utf-8", errors="replace")
    repo_lines = _section_lines(repo_index)
    brief_lines = _section_lines(planner_brief)
    root_name = Path(args.repo_index).resolve().parents[1].parent.name

    payload = {
        "generated_at": "deterministic-local",
        "repo_index_line_count": len(repo_lines),
        "planner_brief_line_count": len(brief_lines),
        "project_name": root_name,
        "signals": [
            "Deterministic Execution",
            "Artifacts",
            "Observability",
        ],
        "hero_claims": [
            "Artifact-first agent collaboration",
            "Replayable event ledger",
            "Remote-safe shell boundaries",
        ],
        "repo_index_preview": repo_lines[:12],
        "planner_brief_preview": brief_lines[:12],
        "launch_ready_summary": f"{root_name} can be presented as a minimal multi-agent kernel instead of a narrow remote helper.",
    }
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
