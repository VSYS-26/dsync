"""Validate Conventional Commit messages for pre-commit commit-msg hook."""

from __future__ import annotations

from pathlib import Path
import re
import sys

CONVENTIONAL_COMMIT_PATTERN = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
    r"(\([a-zA-Z0-9._/-]+\))?"
    r"!?"
    r": "
    r".+$"
)

ALLOWED_PREFIXES = (
    "Merge ",
    "Revert ",
    "fixup! ",
    "squash! ",
    "Initial commit",
)


def _first_non_empty_line(content: str) -> str:
    """Return the first non-empty line from commit message content."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def validate_message(subject: str) -> bool:
    """Return True when a subject line follows allowed commit message formats."""
    if subject.startswith(ALLOWED_PREFIXES):
        return True
    return bool(CONVENTIONAL_COMMIT_PATTERN.match(subject))


def main() -> int:
    """Validate commit message file passed by pre-commit and return exit code."""
    if len(sys.argv) < 2:
        print("Expected path to commit message file as first argument.")
        return 1

    commit_msg_path = Path(sys.argv[1])
    if not commit_msg_path.exists():
        print(f"Commit message file not found: {commit_msg_path}")
        return 1

    subject = _first_non_empty_line(commit_msg_path.read_text(encoding="utf-8"))
    if validate_message(subject):
        return 0

    print("Invalid commit message subject.")
    print("Use Conventional Commits, e.g.:")
    print("  feat(sync): add peer discovery")
    print("  fix(cli): handle missing config")
    print("  docs(readme): document setup")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
