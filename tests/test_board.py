from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from remote_sandbox_framework.board import (
    BOARD_KIND,
    BOARD_VERSION,
    BoardError,
    board_replay,
    board_run,
    board_status,
    load_board_definition,
)
from remote_sandbox_framework.orchestrator import reconcile_manifest, run_scheduler


class BoardTests(unittest.TestCase):
    def _write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def _minimal_board(self, root: Path) -> Path:
        board_path = root / "board.json"
        runtime_dir = root / "runtime" / "artifacts"
        payload = {
            "kind": BOARD_KIND,
            "version": BOARD_VERSION,
            "name": "test-board",
            "artifacts": [
                {"name": "index", "path": "runtime/artifacts/index.md", "format": "text", "required": True},
                {"name": "report", "path": "runtime/artifacts/report.md", "format": "text", "required": True},
            ],
            "tasks": [
                {
                    "id": "scan",
                    "role": "inspector",
                    "mode": "shell_stage",
                    "goal": "write index",
                    "produces": ["index"],
                    "command": "python -c \"from pathlib import Path; Path('runtime/artifacts').mkdir(parents=True, exist_ok=True); Path('runtime/artifacts/index.md').write_text('index', encoding='utf-8')\"",
                    "runner": {"cwd": str(root)},
                    "completion": {"kind": "file_exists", "path": str((runtime_dir / 'index.md').resolve())},
                },
                {
                    "id": "synth",
                    "role": "writer",
                    "mode": "synthesis",
                    "goal": "write report",
                    "depends_on": ["scan"],
                    "inputs": ["index"],
                    "produces": ["report"],
                    "prompt": "Produce a report containing Deterministic Execution, Artifacts, and Observability.",
                },
            ],
        }
        self._write_json(board_path, payload)
        return board_path

    def test_load_board_definition_and_cycle_validation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            board_path = self._minimal_board(root)
            definition = load_board_definition(board_path)
            self.assertEqual(definition.name, "test-board")

            payload = json.loads(board_path.read_text(encoding="utf-8"))
            payload["tasks"][0]["depends_on"] = ["synth"]
            self._write_json(board_path, payload)
            with self.assertRaises(BoardError):
                load_board_definition(board_path)

    def test_board_run_and_replay(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            board_path = self._minimal_board(root)
            run_dir = root / "run"
            code, actual_run_dir = board_run(board_path, run_dir=run_dir, resume=True)
            self.assertEqual(code, 0)
            self.assertEqual(actual_run_dir, run_dir.resolve())

            state = board_status(run_dir)
            self.assertEqual(state["tasks"]["synth"]["status"], "completed")
            replay = board_replay(run_dir)
            self.assertIn("synth", replay["tasks"])
            self.assertTrue((root / "runtime" / "artifacts" / "report.md").exists())

    def test_resume_marks_existing_artifacts_completed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            board_path = self._minimal_board(root)
            artifact = root / "runtime" / "artifacts" / "index.md"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("index", encoding="utf-8")
            run_dir = root / "run"
            board_run(board_path, run_dir=run_dir, resume=True)
            state = board_status(run_dir)
            self.assertEqual(state["tasks"]["scan"]["status"], "completed")

    def test_existing_manifest_scheduler_still_runs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            done_path = root / "runtime" / "smoke" / "hello.done"
            manifest_path = root / "manifest.json"
            payload = {
                "version": 1,
                "resource_slots": {"cpu": 1, "gpu": 0},
                "tasks": [
                    {
                        "name": "hello_local",
                        "priority": 10,
                        "status": "queued",
                        "stages": [
                            {
                                "name": "run",
                                "slot": "cpu",
                                "completion": {"kind": "file_exists", "path": str(done_path.resolve())},
                                "command": f"python -c \"from pathlib import Path; Path(r'{done_path.parent}').mkdir(parents=True, exist_ok=True); Path(r'{done_path}').write_text('ok', encoding='utf-8')\"",
                            }
                        ],
                    }
                ],
            }
            self._write_json(manifest_path, payload)
            rc = run_scheduler(
                manifest_path=manifest_path,
                queue_log=root / "runtime" / "scheduler.log",
                runs_dir=root / "runtime" / "runs",
                poll_seconds=0.01,
                max_idle_polls=20,
            )
            self.assertEqual(rc, 0)
            changed, _summary = reconcile_manifest(manifest_path)
            self.assertGreaterEqual(changed, 0)

    def test_missing_required_artifact_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            board_path = root / "board.json"
            payload = {
                "kind": BOARD_KIND,
                "version": BOARD_VERSION,
                "name": "broken-board",
                "artifacts": [
                    {"name": "report", "path": "runtime/artifacts/report.md", "format": "text", "required": True}
                ],
                "tasks": [
                    {
                        "id": "review",
                        "role": "reviewer",
                        "mode": "review_gate",
                        "goal": "review missing artifact",
                        "inputs": ["report"],
                        "review": {"must_contain": ["Deterministic Execution"]},
                    }
                ],
            }
            self._write_json(board_path, payload)
            with self.assertRaises(BoardError):
                board_run(board_path, run_dir=root / "run", resume=False)


if __name__ == "__main__":
    unittest.main()
