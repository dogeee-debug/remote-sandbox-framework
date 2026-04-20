from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from remote_sandbox_framework.orchestrator import reconcile_manifest  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: python scripts/reconcile_manifest.py <manifest> [--override-failed]")
        return 2
    manifest = Path(argv[0]).resolve()
    override_failed = "--override-failed" in argv[1:]
    changed, summary = reconcile_manifest(manifest, override_failed=override_failed)
    print(f"changed={changed}")
    for line in summary:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
