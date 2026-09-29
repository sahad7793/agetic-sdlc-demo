"""Capture the API's OpenAPI document and report changes against a reviewed baseline."""

import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path("docs/openapi/task-management-v1.json")
API_DLL = ROOT / "src/TaskManagement.Api/bin/Release/net8.0/TaskManagement.Api.dll"


def validate_document(data):
    if not isinstance(data, dict) or not str(data.get("openapi", "")).startswith("3.") or not data.get("paths"):
        raise ValueError("Expected an OpenAPI 3 document with at least one path")
    return data


def capture(output):
    if not API_DLL.is_file():
        raise FileNotFoundError(f"Build the API in Release first: {API_DLL}")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryFile(mode="w+t") as log:
        env = os.environ.copy()
        env.update({
            "ASPNETCORE_ENVIRONMENT": "Development",
            "ASPNETCORE_URLS": f"http://127.0.0.1:{port}",
            "ConnectionStrings__TaskManagement": f"Data Source={Path(directory) / 'tasks.db'}",
            "Database__UseAzureSql": "false",
        })
        process = subprocess.Popen(
            ["dotnet", str(API_DLL)], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT
        )
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                try:
                    with urlopen(f"http://127.0.0.1:{port}/swagger/v1/swagger.json", timeout=2) as response:
                        document = validate_document(json.load(response))
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                    return
                except URLError as error:
                    if isinstance(error, HTTPError):
                        raise
                    time.sleep(0.5)
            log.seek(0)
            raise RuntimeError(f"API did not serve its OpenAPI document:\n{log.read()}")
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)


def baseline_from_revision(revision, destination):
    if not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
        raise ValueError("Base revision must be a full commit SHA")
    subprocess.run(["git", "cat-file", "-e", f"{revision}^{{commit}}"], cwd=ROOT, check=True)
    paths = subprocess.run(
        ["git", "ls-tree", "--name-only", revision, "--", str(BASELINE)],
        cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    if not paths:
        snapshot = ROOT / BASELINE
        if not snapshot.is_file():
            raise FileNotFoundError(f"No baseline in base revision or pull request: {BASELINE}")
        destination.write_bytes(snapshot.read_bytes())
        return True
    with destination.open("wb") as output:
        subprocess.run(["git", "show", f"{revision}:{BASELINE}"], cwd=ROOT, check=True, stdout=output)
    return False


def compare(base, current, oasdiff, report, bootstrap=False):
    validate_document(json.loads(base.read_text(encoding="utf-8")))
    validate_document(json.loads(current.read_text(encoding="utf-8")))
    results = []
    for kind in ("breaking", "changelog"):
        result = subprocess.run(
            [str(oasdiff), kind, "--format", "markdown", "--allow-external-refs=false", str(base), str(current)],
            check=True, capture_output=True, text=True
        )
        results.append(result.stdout.strip())

    lines = ["# OpenAPI contract comparison", ""]
    if bootstrap:
        lines += [
            "> Bootstrap: no committed baseline exists on the base branch. "
            "Compared against this PR's snapshot; review the entire snapshot before merging.",
            "",
        ]
    else:
        lines += ["Compared the generated API contract with the committed baseline from the PR base revision.", ""]
    lines += [
        "Breaking changes are **advisory** and do not fail this check.",
        "",
    ]
    snapshot = ROOT / BASELINE
    if snapshot.is_file() and snapshot.read_bytes() != current.read_bytes():
        lines += [
            "> The PR's committed snapshot differs from the generated contract. "
            "Regenerate and review `docs/openapi/task-management-v1.json` before merging.",
            "",
        ]
    lines += [
        "## Breaking changes",
        "",
        results[0] or "No breaking changes detected.",
        "",
        "## Consumer-facing changes",
        "",
        results[1] or "No consumer-facing changes detected.",
        "",
    ]
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines), encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as output:
            output.write(report.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generator = commands.add_parser("capture")
    generator.add_argument("--output", type=Path, required=True)
    comparator = commands.add_parser("compare")
    comparator.add_argument("--current", type=Path, required=True)
    comparator.add_argument("--oasdiff", type=Path, required=True)
    comparator.add_argument("--report", type=Path, required=True)
    source = comparator.add_mutually_exclusive_group(required=True)
    source.add_argument("--base-revision")
    source.add_argument("--baseline", type=Path)
    args = parser.parse_args()

    try:
        if args.command == "capture":
            capture(args.output)
        elif args.base_revision:
            with tempfile.TemporaryDirectory() as directory:
                base = Path(directory) / "baseline.json"
                bootstrap = baseline_from_revision(args.base_revision, base)
                compare(base, args.current, args.oasdiff, args.report, bootstrap)
        else:
            compare(args.baseline, args.current, args.oasdiff, args.report)
    except subprocess.CalledProcessError as error:
        print(f"OpenAPI check failed: {error}\n{error.stderr or ''}", file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError) as error:
        print(f"OpenAPI check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
