"""Pure application marker schema helpers selected from the original project source."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

FindingSeverity = Literal["error", "warning"]

REGISTRY_FILENAME = ".gtkb-app-isolation.json"

ALLOWED_TYPES = frozenset({"FILE", "DIR"})

REGISTRY_SCHEMA_VERSION = "2.0"

ALLOWED_CLASSIFICATIONS = frozenset(
    {"authoritative_input", "generated_output", "runtime_data", "bounded_temporary_output"}
)

@dataclass(frozen=True)
class AppRootFinding:
    """A structured app-root minimization finding."""

    code: str
    message: str
    path: str | None = None
    severity: FindingSeverity = "error"

    def to_dict(self) -> dict[str, str]:
        payload = {"code": self.code, "message": self.message, "severity": self.severity}
        if self.path:
            payload["path"] = self.path
        return payload

def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result

def _normalize_registry_entries(
    payload: dict[str, Any] | None,
    project_root: Path,
    registry_path: Path,
    findings: list[AppRootFinding],
) -> list[dict[str, Any]]:
    if payload is None:
        return []
    location = _display_path(registry_path, project_root)
    if payload.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        findings.append(
            AppRootFinding(
                "registry_schema_unsupported", f"registry schema_version must be {REGISTRY_SCHEMA_VERSION}", location
            )
        )
    application = _non_empty_string(payload.get("application"))
    if not application:
        findings.append(
            AppRootFinding("application_missing", "registry application must be a non-empty string", location)
        )
    elif application != registry_path.parent.name:
        findings.append(
            AppRootFinding("application_mismatch", "registry application does not match its application slot", location)
        )
    entries = payload.get("top_level_artifacts")
    if not isinstance(entries, list) or not entries:
        findings.append(
            AppRootFinding("top_level_artifacts_invalid", "top_level_artifacts must be a non-empty list", location)
        )
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        entry_path = f"{location}#top_level_artifacts[{index}]"
        if not isinstance(entry, dict):
            findings.append(AppRootFinding("registry_entry_not_object", "registry entry must be an object", entry_path))
            continue
        name = _non_empty_string(entry.get("name"))
        artifact_type = _non_empty_string(entry.get("type"))
        classification = _non_empty_string(entry.get("classification"))
        valid_name = bool(
            name and name not in {".", ".."} and not any(c in name for c in "/\\:") and name == entry.get("name")
        )
        if not valid_name:
            findings.append(
                AppRootFinding("entry_invalid_name", "entry name must be one direct top-level name", entry_path)
            )
        elif name.casefold() in seen:
            findings.append(AppRootFinding("entry_duplicate", f"duplicate registry name: {name}", entry_path))
        seen.add(name.casefold())
        if artifact_type not in ALLOWED_TYPES or artifact_type != entry.get("type"):
            findings.append(
                AppRootFinding("entry_invalid_type", f"entry type must be one of {sorted(ALLOWED_TYPES)}", entry_path)
            )
        if classification not in ALLOWED_CLASSIFICATIONS or classification != entry.get("classification"):
            findings.append(
                AppRootFinding(
                    "entry_invalid_classification",
                    f"classification must be one of {sorted(ALLOWED_CLASSIFICATIONS)}",
                    entry_path,
                )
            )
        if "bucket" in entry:
            findings.append(
                AppRootFinding(
                    "entry_retired_bucket", "A/B/C/D buckets are retired; use the current classification", entry_path
                )
            )
        if not _non_empty_string(entry.get("purpose")):
            findings.append(
                AppRootFinding("entry_missing_purpose", "every classification requires a non-empty purpose", entry_path)
            )
        if valid_name and artifact_type in ALLOWED_TYPES:
            normalized.append(dict(entry))
    return normalized

def _non_empty_string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""

def _display_path(path: Path, project_root: Path) -> str:
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return path.as_posix()
