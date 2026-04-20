# Repo Research Demo

This demo shows the Phase 1 positioning of `remote-sandbox-framework` as a minimal autoresearch multi-agent kernel.

It runs a deterministic artifact-first board:

1. `repo_scan` builds a compact repo index.
2. `planner` turns that index into a short brief.
3. `evidence_extract` converts the brief plus repo facts into structured JSON.
4. `synthesis` writes a shareable report.
5. `review` enforces a tiny quality gate.

## Quickstart

```bash
rsf board-run --manifest examples/autoresearch/repo-research/board.json
rsf board-status --run-dir runtime/board-runs/repo-research-YYYYMMDD-HHMMSS
rsf board-replay --run-dir runtime/board-runs/repo-research-YYYYMMDD-HHMMSS
```

The demo works without an LLM. If `--base-url`, `--api-key`, and `--model` are not provided, the framework uses deterministic local fallback generation for the `planner` and `synthesis` tasks.

## Outputs

- `runtime/board-runs/<run_id>/events.ndjson`
- `runtime/board-runs/<run_id>/state.json`
- `runtime/board-runs/<run_id>/summary.json`
- `examples/autoresearch/repo-research/runtime/artifacts/repo-index.md`
- `examples/autoresearch/repo-research/runtime/artifacts/evidence.json`
- `examples/autoresearch/repo-research/runtime/artifacts/report.md`
