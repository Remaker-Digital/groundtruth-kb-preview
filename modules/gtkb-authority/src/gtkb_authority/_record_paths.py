from __future__ import annotations
import json
from collections.abc import Iterable, Mapping
from typing import Any

def registry_path_observations(
    *,
    specifications: Iterable[Mapping[str, Any]],
    tests: Iterable[Mapping[str, Any]],
    documents: Iterable[Mapping[str, Any]],
    project_artifact_links: Iterable[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Extract typed current path fields, without interpreting narrative prose."""
    observations: dict[tuple[str, str, str, str], dict[str, str]] = {}

    def append(source_kind: str, source_id: Any, field: str, value: Any) -> None:
        if value is None:
            return
        if not isinstance(value, str):
            raise ValueError(f"{source_kind}:{source_id}:{field} must contain a path string")
        path = value.strip().split("::", 1)[0]
        if path:
            row = {"path": path, "source_kind": source_kind, "source_id": str(source_id), "field": field}
            observations[(path.casefold(), source_kind, str(source_id), field)] = row

    for spec in specifications:
        values = spec.get("source_paths")
        if values is None:
            continue
        if isinstance(values, str):
            try:
                values = json.loads(values)
            except json.JSONDecodeError as exc:
                raise ValueError(f"specification:{spec['id']}:source_paths is not valid JSON") from exc
        if not isinstance(values, (list, tuple)):
            raise ValueError(f"specification:{spec['id']}:source_paths must be a list of paths")
        for value in values:
            if not isinstance(value, str):
                raise ValueError(f"specification:{spec['id']}:source_paths must be a list of paths")
            append("specification", spec["id"], "source_paths", value)
    for test in tests:
        append("test", test["id"], "test_file", test.get("test_file"))
    for document in documents:
        append("document", document["id"], "source_path", document.get("source_path"))
    path_types = {"configuration", "document", "file", "path", "source_file", "test"}
    for link in project_artifact_links:
        if link.get("status") == "active" and str(link.get("artifact_type") or "").casefold() in path_types:
            append("project_artifact_link", link["id"], "artifact_ref", link.get("artifact_ref"))
    return [observations[key] for key in sorted(observations)]
