from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .openai_compat import OpenAICompatClient, extract_first_text
from .orchestrator import load_manifest


PROPOSAL_SYSTEM_PROMPT = """You are a cautious remote experiment planning assistant.
You never execute commands. You only propose manifest tasks in JSON.
Return strict JSON only, no markdown fences.
Each proposed task must be deterministic, auditable, and non-destructive by default.
Prefer extending an existing manifest schema with resource slots, smoke checks, and file-based completion criteria.
"""


@dataclass(slots=True)
class ManifestProposalRequest:
    goal: str
    constraints: list[str]
    manifest_path: Path | None = None
    max_new_tasks: int = 5


def build_proposal_prompt(req: ManifestProposalRequest) -> str:
    manifest_excerpt = ""
    if req.manifest_path and req.manifest_path.exists():
        manifest = load_manifest(req.manifest_path)
        compact = {
            "resource_slots": manifest.get("resource_slots"),
            "runner_profiles": manifest.get("runner_profiles"),
            "task_names": [task.get("name") for task in manifest.get("tasks", [])[:20]],
        }
        manifest_excerpt = json.dumps(compact, ensure_ascii=False, indent=2)
    constraints = "\n".join(f"- {item}" for item in req.constraints)
    return (
        f"Goal:\n{req.goal}\n\n"
        f"Constraints:\n{constraints}\n\n"
        f"Current manifest excerpt:\n{manifest_excerpt or '{}'}\n\n"
        f"Return a JSON object with keys: summary, assumptions, tasks.\n"
        f"tasks must be a list of at most {req.max_new_tasks} new manifest task objects.\n"
        f"Do not include explanations outside JSON.\n"
    )


def propose_tasks(client: OpenAICompatClient, req: ManifestProposalRequest) -> dict[str, Any]:
    response = client.chat_completion(
        system_prompt=PROPOSAL_SYSTEM_PROMPT,
        user_prompt=build_proposal_prompt(req),
        temperature=0.0,
        max_tokens=1600,
    )
    text = extract_first_text(response).strip()
    if not text:
        raise RuntimeError("Assistant returned empty content.")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Assistant did not return valid JSON: {text[:400]}") from exc
