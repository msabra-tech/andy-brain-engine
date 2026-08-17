"""Safe, approval-gated evolution of the local Andy Brain engine.

Claude can draft a unified diff, but the live engine is never changed by the
draft. The diff is first applied and tested in an isolated workspace. Only an
explicitly approved proposal may install that exact tested diff, after a backup.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import Config


WORKSPACES = Path("data/workspaces/system-changes")
BACKUPS = Path("data/backups/engine")
ALLOWED_PREFIXES = ("src/", "tests/", "docs/", "scripts/", "templates/", "prompts/", "config/")
ALLOWED_FILES = {"README.md", "AGENTS.md", ".gitignore", "brain", "Setup Andy Brain.cmd", "Run Andy Review.cmd"}


def _safe_patch_path(raw_path: str) -> str:
    path = raw_path.strip().split("\t", 1)[0]
    if path in {"/dev/null", ""}:
        return ""
    if path.startswith(("a/", "b/")):
        path = path[2:]
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or path.startswith(".git/") or path.startswith("data/"):
        raise ValueError(f"system change cannot modify protected path: {raw_path}")
    if path.endswith(".local.json") or path.endswith(".dpapi"):
        raise ValueError(f"system change cannot modify local credential/config path: {raw_path}")
    if path not in ALLOWED_FILES and not path.endswith(".cmd") and not path.startswith(ALLOWED_PREFIXES):
        raise ValueError(f"system change path is outside the allowed engine surface: {raw_path}")
    return path


def validate_patch(patch: str) -> list[str]:
    if not patch.strip():
        raise ValueError("system change must contain a unified diff")
    if "GIT binary patch" in patch or "\x00" in patch:
        raise ValueError("binary system changes are not supported")
    affected: list[str] = []
    for line in patch.splitlines():
        if line.startswith(("+++ ", "--- ")):
            path = _safe_patch_path(line[4:])
            if path and path not in affected:
                affected.append(path)
    if not affected or "diff --git" not in patch:
        raise ValueError("system change must be a standard unified Git diff with at least one allowed file")
    return affected


def _ignore_engine(directory: str, names: list[str]) -> set[str]:
    current = Path(directory)
    ignored = {".git", "data", "__pycache__"}
    if current.name == "config":
        ignored.update(name for name in names if name.endswith(".local.json"))
    return ignored


def _run(command: list[str], cwd: Path) -> dict[str, Any]:
    result = subprocess.run(command, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    return {"command": command, "ok": result.returncode == 0, "output": result.stdout[-4000:], "exit_code": result.returncode}


def _checks(workspace: Path) -> list[dict[str, Any]]:
    return [
        _run([sys.executable, "-m", "compileall", "-q", "src", "scripts"], workspace),
        _run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"], workspace),
    ]


def _apply_patch(workspace: Path, patch_path: Path, check_only: bool = False) -> dict[str, Any]:
    # --no-index prevents Git from walking up from data/workspaces into the live
    # engine repository. Patches therefore apply relative to this exact directory.
    command = ["git", "apply", "--no-index", "--unsafe-paths"]
    if check_only:
        command.append("--check")
    command.append(str(patch_path))
    return _run(command, workspace)


def stage_system_change(config: Config, proposal_id: str, patch: str) -> dict[str, Any]:
    """Apply a Claude patch and run checks in a disposable workspace, never live."""
    affected = validate_patch(patch)
    workspace = config.engine / WORKSPACES / proposal_id
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(config.engine, workspace, ignore=_ignore_engine)
    patch_path = workspace / ".andy-brain-system-change.patch"
    patch_path.write_text(patch, encoding="utf-8")
    precheck = _apply_patch(workspace, patch_path, check_only=True)
    applied = _apply_patch(workspace, patch_path) if precheck["ok"] else {"ok": False, "command": [], "output": "Patch precheck failed; no workspace changes applied.", "exit_code": precheck["exit_code"]}
    checks = _checks(workspace) if applied["ok"] else []
    return {
        "workspace": str(workspace),
        "affected_files": affected,
        "patch_check": precheck,
        "patch_applied_in_workspace": applied["ok"],
        "checks": checks,
        "passed": bool(applied["ok"] and all(check["ok"] for check in checks)),
        "approval_required": True,
    }


def _backup_engine(config: Config) -> Path:
    from .operations import now

    destination = config.engine / BACKUPS / now().replace(":", "-")
    shutil.copytree(config.engine, destination, ignore=_ignore_engine)
    return destination


def install_system_change(config: Config, proposal_id: str, patch: str, preflight: dict[str, Any]) -> dict[str, Any]:
    """Install exactly the preflighted patch after proposal confirmation."""
    if not preflight.get("passed"):
        raise ValueError("system change cannot be installed because isolated preflight did not pass")
    workspace = config.engine / WORKSPACES / proposal_id
    patch_path = workspace / ".andy-brain-system-change.patch"
    if not patch_path.exists() or patch_path.read_text(encoding="utf-8") != patch:
        raise ValueError("the tested system-change patch is unavailable or does not match the proposal")
    live_check = _apply_patch(config.engine, patch_path, check_only=True)
    if not live_check["ok"]:
        raise ValueError("live engine changed since preflight; re-stage the system change before installation")
    backup = _backup_engine(config)
    applied = _apply_patch(config.engine, patch_path)
    if not applied["ok"]:
        raise RuntimeError("system change could not be installed after backup")
    checks = _checks(config.engine)
    if not all(check["ok"] for check in checks):
        rollback = _run(["git", "apply", "--unsafe-paths", "--reverse", str(patch_path)], config.engine)
        raise RuntimeError(f"installed system change failed validation and was rolled back: {rollback['output']}")
    return {"backup": str(backup), "checks": checks, "affected_files": preflight.get("affected_files", [])}
