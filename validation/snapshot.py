"""Capture reproducible source, syntax, lint and catalogue baseline reports."""

import argparse
import ast
from collections import Counter
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any

from validation.catalogue import ROOT, validate_catalogue

SOURCE_DIRECTORIES = ("core", "ui", "util", "validation", "tests")


def capture() -> dict[str, Any]:
    """Return source hashes and diagnostics without importing application entry points."""
    paths = sorted(
        {
            *ROOT.glob("*.py"),
            *(
                path
                for directory in SOURCE_DIRECTORIES
                for path in (ROOT / directory).rglob("*.py")
            ),
        }
    )
    inventory = []
    for path in paths:
        source = path.read_text(encoding="utf-8-sig")
        syntax_error = None
        try:
            ast.parse(source, filename=path.name)
        except SyntaxError as error:
            syntax_error = str(error)
        inventory.append(
            {
                "file": path.relative_to(ROOT).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "lines": len(source.splitlines()),
                "syntax_error": syntax_error,
            }
        )
    lint = subprocess.run(
        [sys.executable, "-m", "ruff", "check", ".", "--output-format", "json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if lint.returncode not in (0, 1):
        raise RuntimeError(lint.stderr)
    diagnostics = [
        {
            "file": Path(item["filename"]).relative_to(ROOT).as_posix(),
            "code": item["code"],
            "line": item["location"]["row"],
            "message": item["message"],
        }
        for item in json.loads(lint.stdout)
    ]
    return {
        "catalogue_hashes": {
            path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for directory in (ROOT / "data", ROOT / "validation" / "schemas")
            for path in sorted(directory.rglob("*.json"))
        },
        "python": platform.python_version(),
        "platform": platform.system(),
        "dependencies": {
            name: version(name)
            for name in ("PySide6", "jsonschema", "pytest", "pytest-qt", "ruff")
        },
        "source_files": len(inventory),
        "source_lines": sum(item["lines"] for item in inventory),
        "inventory": inventory,
        "lint_counts": dict(sorted(Counter(item["code"] for item in diagnostics).items())),
        "lint": diagnostics,
    }


def main() -> int:
    """Write an explicit snapshot; never use historical errors as a publication exemption."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    source = capture()
    catalogue = validate_catalogue(ROOT / "data", ROOT / "validation" / "schemas").to_dict()
    for name, report in (("source", source), ("catalogue", catalogue)):
        (args.output / f"{name}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(
        f"Captured {source['source_files']} source files, {len(source['lint'])} lint diagnostics, "
        f"{len(catalogue['diagnostics'])} catalogue diagnostics"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
