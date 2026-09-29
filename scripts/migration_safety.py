#!/usr/bin/env python3
"""Report potentially destructive operations in EF Core migrations added by a PR."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = PurePosixPath("src/TaskManagement.Api/Migrations")
MIGRATION_FILE = re.compile(r"(?P<id>[0-9]{14}_[A-Za-z0-9_]+)\.cs\Z")
BASE_SHA = re.compile(r"[0-9a-f]{40,64}\Z")
UP_METHOD = re.compile(
    r"\bprotected\s+override\s+void\s+Up\s*\([^)]*\)\s*\{(?P<body>.*?)"
    r"(?=\bprotected\s+override\s+void\s+Down\s*\(|\Z)",
    re.DOTALL,
)
COMMENTS = re.compile(r"//[^\r\n]*|/\*.*?\*/", re.DOTALL)
DESTRUCTIVE_OPERATIONS = {
    "DropColumn": "drops column data",
    "DropTable": "drops table data",
    "RenameColumn": "may break readers or writers expecting the previous column name",
    "RenameTable": "may break readers or writers expecting the previous table name",
    "AlterColumn": "may change the SQL type, nullability, or stored values",
    "DropIndex": "may remove uniqueness or query-performance guarantees",
}
OPERATION_CALL = re.compile(
    r"\bmigrationBuilder\s*\.\s*(?P<operation>"
    + "|".join(DESTRUCTIVE_OPERATIONS)
    + r")(?:\s*<[^>]+>)?\s*\("
)


class MigrationSafetyError(ValueError):
    """Raised when migration evidence cannot be determined reliably."""


def _git(*arguments: str, root: Path = ROOT) -> str:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        detail = getattr(error, "stderr", None)
        if isinstance(detail, str) and detail.strip():
            detail = detail.strip()
        else:
            detail = str(error)
        raise MigrationSafetyError(f"Git could not determine migration changes: {detail}") from error
    return result.stdout


def _validate_base_sha(base_sha: str) -> None:
    if not BASE_SHA.fullmatch(base_sha):
        raise MigrationSafetyError("Base revision must be a full hexadecimal commit SHA.")


def _migration_ids(paths: list[str]) -> list[str]:
    ids = set()
    for path in paths:
        relative = PurePosixPath(path)
        if relative.parent != MIGRATIONS_DIR:
            continue
        match = MIGRATION_FILE.fullmatch(relative.name)
        if match:
            ids.add(match.group("id"))
    return sorted(ids)


def added_migration_ids(base_sha: str, *, root: Path = ROOT) -> list[str]:
    """Return migration IDs for newly added migration source files, in EF order."""
    _validate_base_sha(base_sha)
    changed = _git(
        "diff",
        "--name-only",
        "-z",
        "--diff-filter=A",
        f"{base_sha}...HEAD",
        "--",
        str(MIGRATIONS_DIR),
        root=root,
    )
    return _migration_ids(changed.split("\0"))


def latest_base_migration(base_sha: str, *, root: Path = ROOT) -> str:
    """Return the latest migration ID present at the trusted base revision, or 0."""
    _validate_base_sha(base_sha)
    listing = _git(
        "ls-tree",
        "-r",
        "--name-only",
        base_sha,
        "--",
        str(MIGRATIONS_DIR),
        root=root,
    )
    ids = _migration_ids(listing.splitlines())
    return ids[-1] if ids else "0"


def destructive_operations(source: str) -> list[tuple[int, str, str]]:
    """Return potentially destructive migration operations inside the Up method."""
    match = UP_METHOD.search(source)
    if not match:
        return []

    body = COMMENTS.sub(lambda comment: "\n" * comment.group(0).count("\n"), match.group("body"))
    line_offset = source.count("\n", 0, match.start("body"))
    findings = []
    for call in OPERATION_CALL.finditer(body):
        operation = call.group("operation")
        line = line_offset + body.count("\n", 0, call.start()) + 1
        findings.append((line, operation, DESTRUCTIVE_OPERATIONS[operation]))
    return findings


def render_report(base_sha: str, *, root: Path = ROOT) -> str:
    ids = added_migration_ids(base_sha, root=root)
    lines = [
        "## EF Core migration safety (advisory)",
        "",
        f"Trusted comparison base: `{base_sha}`.",
        "",
        "Potentially destructive operations are warnings only; this report does not block the pull request.",
        "",
    ]
    if not ids:
        lines.extend(["No new EF Core migration source files were added.", ""])
    else:
        lines.append("New migrations: " + ", ".join(f"`{migration_id}`" for migration_id in ids) + ".")
        lines.append("")
        findings = []
        for migration_id in ids:
            path = root / MIGRATIONS_DIR / f"{migration_id}.cs"
            try:
                source = path.read_text(encoding="utf-8-sig")
            except OSError as error:
                raise MigrationSafetyError(f"Could not read migration {path}: {error}") from error
            for line, operation, impact in destructive_operations(source):
                findings.append((str(path.relative_to(root)), line, operation, impact))
        if findings:
            lines.extend(
                [
                    "| Migration | Line | Operation | Potential impact |",
                    "| --- | ---: | --- | --- |",
                ]
            )
            lines.extend(
                f"| `{path}` | {line} | `{operation}` | {impact} |"
                for path, line, operation, impact in findings
            )
            lines.append("")
        else:
            lines.extend(["No configured destructive operations were detected in the new migrations' `Up` methods.", ""])
    lines.extend(
        [
            "This static scan may miss destructive raw SQL or custom migration code and may flag operations whose impact is mitigated by application-specific handling. Review the migration and generated SQL before approval.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("list-new", "previous"):
        command = commands.add_parser(name)
        command.add_argument("--base-revision", required=True)
    report = commands.add_parser("report")
    report.add_argument("--base-revision", required=True)
    report.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    try:
        if args.command == "list-new":
            ids = added_migration_ids(args.base_revision)
            previous = latest_base_migration(args.base_revision)
            if previous != "0" and any(migration_id <= previous for migration_id in ids):
                raise MigrationSafetyError(
                    "A new migration sorts before an existing base migration; cannot generate a safe incremental script."
                )
            print("\n".join(ids))
        elif args.command == "previous":
            print(latest_base_migration(args.base_revision))
        else:
            report_text = render_report(args.base_revision)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(report_text, encoding="utf-8")
            print(report_text, end="")
    except MigrationSafetyError as error:
        print(f"migration_safety: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
