from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a compact repository index.")
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    output = Path(args.output).resolve()
    files = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and ".git/" not in path.as_posix() and "__pycache__" not in path.as_posix()
    ]
    files.sort()
    lines = [
        "# Repository Index",
        "",
        f"Root: `{root}`",
        f"File count: {len(files)}",
        "",
        "## Files",
        "",
    ]
    lines.extend(f"- `{item}`" for item in files[:200])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
