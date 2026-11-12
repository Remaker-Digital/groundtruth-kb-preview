from __future__ import annotations
import os
import re
import subprocess
import time
import tomllib
from pathlib import Path
from typing import Any
from ._slot_validation import check_slot_markers
from ._deadlines import expired

class ApplicationRegistryError(ValueError):
    """The configured catalog or application slot cannot be interpreted safely."""


def application_slot_path(project_root: Path, app_name: str) -> Path:
    """Resolve one named immediate child without treating package location as host."""
    if not isinstance(app_name, str) or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", app_name) is None:
        raise ApplicationRegistryError("application name must be one hosted slot identifier")
    if app_name.casefold() == "gtkb":
        raise ApplicationRegistryError("gtkb is reserved for the platform initialization subject")
    host = Path(project_root).resolve()
    applications = host / "applications"
    slot = applications / app_name
    try:
        if applications.resolve() != applications or slot.resolve() != slot:
            raise ApplicationRegistryError("application slot must not be redirected from the selected host")
        if applications.exists() and not applications.is_dir():
            raise ApplicationRegistryError("the selected host's applications path is not a directory")
        if slot.exists() and not slot.is_dir():
            raise ApplicationRegistryError(f"application slot {app_name} is not a directory")
    except (OSError, RuntimeError) as exc:
        raise ApplicationRegistryError("application slot boundary cannot be resolved") from exc
    return slot


def load_application_catalog(project_root: Path) -> dict[str, dict[str, str]]:
    """Read current catalog entries; malformed data never means an empty catalog."""
    host = Path(project_root).resolve()
    applications = host / "applications"
    path = applications / "registry.toml"
    try:
        if applications.resolve() != applications or path.resolve() != path:
            raise ApplicationRegistryError("application catalog must remain directly under the selected host")
        if not path.exists():
            return {}
        if not path.is_file():
            raise ApplicationRegistryError("applications/registry.toml is not a file")
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        raise ApplicationRegistryError(f"application catalog cannot be read: {exc}") from exc
    entries = payload.get("applications")
    if not isinstance(entries, dict):
        raise ApplicationRegistryError("application catalog requires an applications table")
    seen: set[str] = set()
    for name, entry in entries.items():
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name) is None:
            raise ApplicationRegistryError("application catalog contains an invalid slot identifier")
        if name.casefold() == "gtkb":
            raise ApplicationRegistryError("application catalog cannot register the reserved gtkb subject")
        if name.casefold() in seen:
            raise ApplicationRegistryError("application catalog contains duplicate application names")
        seen.add(name.casefold())
        if not isinstance(entry, dict) or entry.get("slot") != name:
            raise ApplicationRegistryError(f"application catalog entry {name} must identify its same-named hosted slot")
    return entries

def validate_application_scope(project_root: Path | None, scope: str | None, *, require_explicit: bool = False) -> None:
    """Validate a record's classification; this grants no repository effects.

    Null remains unresolved for historical import validation. Current writers
    can require explicit record scope. Application names come from the current
    catalog, never a platform enumeration or a guessed path association.
    """
    if scope is None:
        if require_explicit:
            raise ApplicationRegistryError(
                "application_scope_required: explicitly declare gtkb_platform or application:<catalog name>"
            )
        return
    if scope == "gtkb_platform":
        return
    if not isinstance(scope, str) or re.fullmatch(r"application:[A-Za-z][A-Za-z0-9_-]*", scope) is None:
        raise ApplicationRegistryError("application_scope must be gtkb_platform or application:<catalog name>")
    if project_root is None:
        raise ApplicationRegistryError("application scope validation requires the configured platform host")
    name = scope.removeprefix("application:")
    application_slot_path(project_root, name)
    if name not in load_application_catalog(project_root):
        raise ApplicationRegistryError("application_scope must identify an exact current application catalog entry")


def resolve_project_repository(project_root: Path, repository_ref: str, *, probe_seconds: float, deadline: float | None, checkpoint: Any) -> Path:
    """Resolve an explicit current project fact against this host's catalog.

    This read grants no claim or commit permission. Callers re-read the project
    and resolve again at the effect boundary; no absolute path is persisted.
    """

    host = Path(project_root).absolute()
    if host.resolve() != host:
        raise ApplicationRegistryError("the platform host must not be redirected")
    if repository_ref == "platform":
        root = host
    else:
        if not isinstance(repository_ref, str) or not repository_ref.startswith("application:"):
            raise ApplicationRegistryError("repository_ref must be platform or application:<catalog name>")
        name = repository_ref.removeprefix("application:")
        root = application_slot_path(host, name)
        if name not in load_application_catalog(host):
            raise ApplicationRegistryError("repository_ref must identify an exact current application catalog entry")
        markers = check_slot_markers(host, name)
        if not markers["app_toml_present"] or not markers["consistent"]:
            raise ApplicationRegistryError("the application repository requires matching current application markers")
        if not (root / ".git").is_dir():
            raise ApplicationRegistryError("the application slot requires its own independent Git repository")
    try:
        if not root.is_dir() or (root / ".git").resolve() != root / ".git":
            raise ApplicationRegistryError("the selected repository root or Git directory is unavailable or redirected")
        checkpoint()
        if expired(deadline):
            raise ApplicationRegistryError("the original repository operation expired")
        remaining = probe_seconds if deadline is None else min(probe_seconds, max(0.0, deadline - time.monotonic()))
        result = subprocess.run(
            [
                "git",
                "--no-optional-locks",
                "rev-parse",
                "--show-toplevel",
                "--path-format=absolute",
                "--git-common-dir",
            ],
            cwd=root,
            env={key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")},
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=remaining,
        )
        checkpoint()
        if expired(deadline):
            raise ApplicationRegistryError("the original repository operation expired")
        lines = result.stdout.splitlines()
        if result.returncode or len(lines) != 2 or Path(lines[0]).resolve() != root:
            raise ApplicationRegistryError("the selected path is not its exact Git checkout root")
        if repository_ref != "platform" and Path(lines[1]).resolve() != root / ".git":
            raise ApplicationRegistryError("the application must have its own independent Git common directory")
    except (OSError, UnicodeError, RuntimeError, subprocess.TimeoutExpired) as exc:
        raise ApplicationRegistryError("the selected repository identity could not be observed") from exc
    return root
