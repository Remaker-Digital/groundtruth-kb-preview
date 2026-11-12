"""Program planning v0 over existing canonical projects and their history.

Pure field builders and read-only reports. The normal authority writers own CAS,
identity, scope, references, history and effects. No verdicts or independent store.
"""

from __future__ import annotations

import copy
import json
import math
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

START = "<!-- GTKB_PROGRAM_PLAN_V0 -->"
END = "<!-- /GTKB_PROGRAM_PLAN_V0 -->"
SCHEMA = "gtkb.program-plan.v0"
CAUSES = ("scope", "discovered_necessity", "estimation_error", "waiting", "rework")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z")
OID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
MAX_UNITS = 500
MAX_FORECAST_DAYS = 3660


class PlanningError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fields(value: Any, names: set[str]) -> None:
    if type(value) is not dict or set(value) != names:
        raise PlanningError("invalid_planning_shape")


def _optional_fields(value: Any, required: set[str], optional: set[str]) -> None:
    if type(value) is not dict or not required <= set(value) or set(value) - required - optional:
        raise PlanningError("invalid_planning_shape")


def _number(value: Any, *, signed: bool = False) -> float:
    # Normal API/JSONB parsing produces Decimal fractions. Normalize only after
    # finite/bounds checks; booleans and numeric strings remain invalid.
    if (
        type(value) not in (int, float, Decimal)
        or (type(value) is Decimal and not value.is_finite())
        or abs(value) > 1e9
        or not math.isfinite(value)
        or (not signed and value < 0)
    ):
        raise PlanningError("invalid_planning_number")
    return float(value)


def _identifier(value: Any) -> str:
    if type(value) is not str or ID.fullmatch(value) is None:
        raise PlanningError("invalid_planning_reference")
    return value


def _version(value: Any) -> int:
    if type(value) is not int or not 1 <= value < 2147483647:
        raise PlanningError("invalid_planning_version")
    return value


def _time(value: Any) -> datetime:
    if type(value) is not str:
        raise PlanningError("invalid_planning_time")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise PlanningError("invalid_planning_time") from None
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise PlanningError("planning_time_requires_utc")
    return result.astimezone(UTC)


def _reference(value: Any) -> dict[str, Any]:
    _fields(value, {"id", "version"})
    return {"id": _identifier(value["id"]), "version": _version(value["version"])}


def _basis(value: Any, evidence: dict[tuple[str, str], dict[str, Any]] | None) -> dict[str, Any]:
    _optional_fields(value, {"kind", "reason", "evidence_refs"}, {"provenance"})
    if (
        type(value["kind"]) is not str
        or value["kind"] not in {"judgment", "reference_class", "measured", "derived", "rule", "prior", "assumption"}
        or type(value["reason"]) is not str
        or not 1 <= len(value["reason"]) <= 4096
    ):
        raise PlanningError("invalid_estimate_basis")
    if "provenance" in value and (
        type(value["provenance"]) is not str or not 1 <= len(value["provenance"].strip()) <= 4096
    ):
        raise PlanningError("invalid_estimate_basis")
    refs = value["evidence_refs"]
    if type(refs) is not list or len(refs) > 100 or (value["kind"] == "reference_class" and not refs):
        raise PlanningError("invalid_estimate_basis")
    seen = set()
    for ref in refs:
        _fields(ref, {"domain", "id", "version"})
        if type(ref["domain"]) is not str or ref["domain"] not in {"projects", "work-items", "bridge-attempts"}:
            raise PlanningError("invalid_planning_reference")
        key = ref["domain"], _identifier(ref["id"])
        if key in seen:
            raise PlanningError("duplicate_planning_reference")
        seen.add(key)
        if ref["version"] is None:
            if ref["domain"] != "bridge-attempts":
                raise PlanningError("invalid_planning_version")
        else:
            _version(ref["version"])
        if evidence is not None:
            row = evidence.get(key)
            if (
                not row
                or row.get("id") != ref["id"]
                or (ref["version"] is not None and row.get("version") != ref["version"])
            ):
                raise PlanningError("estimate_evidence_changed")
    return copy.deepcopy(value)


def _probability(value: Any, evidence=None) -> dict[str, Any] | None:
    if value is None:
        return None
    _fields(value, {"value", "basis"})
    probability = _number(value["value"])
    if probability > 1:
        raise PlanningError("invalid_success_probability")
    return {"value": probability, "basis": _basis(value["basis"], evidence)}


def validate_estimate(
    value: Any, *, evidence: dict[tuple[str, str], dict[str, Any]] | None = None
) -> dict[str, Any] | None:
    """Validate the one nullable JSONB field. Unknown waiting is a pair of nulls.

    The normal writer also supplies actual referenced records to validate their
    identity/version. Reference-class elapsed time never becomes productive effort.
    """
    if value is None:
        return None
    if type(value) is not dict or "schema_version" not in value:
        raise PlanningError("invalid_planning_shape")
    if type(value["schema_version"]) is not int:
        raise PlanningError("invalid_estimate_schema")
    if value["schema_version"] not in {1, 2}:
        raise PlanningError("invalid_estimate_schema")
    duration_key = "effort_hours" if value["schema_version"] == 1 else "elapsed_hours"
    _optional_fields(value, {"schema_version", duration_key, "waiting"}, {"success_probability"})
    duration = value[duration_key]
    normalized = None
    if duration is not None or value["schema_version"] == 1:
        _fields(duration, {"p50", "p90", "basis"})
        p50, p90 = _number(duration["p50"]), _number(duration["p90"])
        if p90 < p50:
            raise PlanningError("estimate_range_reversed")
        normalized = {"p50": p50, "p90": p90, "basis": _basis(duration["basis"], evidence)}
    result = {"schema_version": value["schema_version"], duration_key: normalized, "waiting": []}
    if "success_probability" in value:
        result["success_probability"] = _probability(value["success_probability"], evidence)
    waiting = value["waiting"]
    if type(waiting) is not list or len(waiting) > 20:
        raise PlanningError("invalid_waiting_estimate")
    seen = set()
    for row in waiting:
        _fields(row, {"cause", "p50_hours", "p90_hours", "basis"})
        cause = row["cause"]
        if type(cause) is not str or not 1 <= len(cause) <= 128 or cause in seen:
            raise PlanningError("invalid_waiting_estimate")
        seen.add(cause)
        low, high = row["p50_hours"], row["p90_hours"]
        if low is None and high is None:
            pass
        elif low is None or high is None:
            raise PlanningError("invalid_waiting_estimate")
        else:
            low, high = _number(low), _number(high)
            if high < low:
                raise PlanningError("estimate_range_reversed")
        result["waiting"].append(
            {"cause": cause, "p50_hours": low, "p90_hours": high, "basis": _basis(row["basis"], evidence)}
        )
    return result


def estimate_bases(value: Any) -> list[dict[str, Any]]:
    """Collect all estimate bases for ordinary canonical evidence validation."""
    normalized = validate_estimate(value)
    if normalized is None:
        return []
    duration = normalized.get("effort_hours", normalized.get("elapsed_hours"))
    bases = [duration["basis"]] if duration is not None else []
    bases += [row["basis"] for row in normalized["waiting"]]
    if normalized.get("success_probability") is not None:
        bases.append(normalized["success_probability"]["basis"])
    return bases


def _pace(value: Any, evidence=None) -> dict[str, Any]:
    if type(value) is not dict:
        raise PlanningError("invalid_program_pace")
    if "elapsed_hours_per_day" in value:
        _fields(value, {"elapsed_hours_per_day", "days", "basis"})
        hours = _number(value["elapsed_hours_per_day"])
        if not 0 < hours <= 24:
            raise PlanningError("invalid_program_pace")
        result = {"elapsed_hours_per_day": hours, "days": value["days"], "basis": _basis(value["basis"], evidence)}
    else:
        _fields(value, {"productive_hours_per_day", "days"})
        if type(value["productive_hours_per_day"]) is bool or _number(value["productive_hours_per_day"]) != 8:
            raise PlanningError("invalid_program_pace")
        result = {"productive_hours_per_day": 8, "days": value["days"]}
    if type(result["days"]) is not str or result["days"] not in {"every_day", "weekdays"}:
        raise PlanningError("invalid_program_pace")
    return result


def pace_bases(value: Any) -> list[dict[str, Any]]:
    """Validate the pace shape before its canonical references are resolved."""
    normalized = _pace(value)
    return [normalized["basis"]] if "basis" in normalized else []


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PlanningError("duplicate_plan_key")
        result[key] = value
    return result


def _finite_json_number(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise PlanningError("invalid_planning_number")
    return result


def _validate_snapshot(value: Any) -> None:
    required = {"as_of", "pace", "buffer_hours", "p50", "p90", "units", "findings", "model"}
    if (
        type(value) is not dict
        or not required <= set(value)
        or set(value) - required not in (set(), {"remaining_effort_hours"}, {"remaining_elapsed_hours"})
    ):
        raise PlanningError("invalid_forecast_snapshot")
    _time(value["as_of"])
    _pace(value["pace"])
    if "elapsed_hours_per_day" not in value["pace"] and type(value["pace"].get("productive_hours_per_day")) is not int:
        raise PlanningError("invalid_program_pace")
    _fields(value["buffer_hours"], {"p50", "p90"})
    if _number(value["buffer_hours"]["p90"]) < _number(value["buffer_hours"]["p50"]):
        raise PlanningError("estimate_range_reversed")
    if type(value["units"]) is not list or len(value["units"]) > MAX_UNITS:
        raise PlanningError("invalid_forecast_snapshot")
    units = set()
    for row in value["units"]:
        required_unit = {"project_id", "project_version", "committed"}
        if (
            type(row) is not dict
            or not required_unit <= set(row)
            or set(row) - required_unit not in (set(), {"p50_date", "p90_date"})
            or type(row["committed"]) is not bool
        ):
            raise PlanningError("invalid_forecast_snapshot")
        key = _identifier(row["project_id"])
        _version(row["project_version"])
        if key in units:
            raise PlanningError("duplicate_planning_reference")
        units.add(key)
        if "p50_date" in row and _time(row["p90_date"]) < _time(row["p50_date"]):
            raise PlanningError("estimate_range_reversed")
    known = value["p50"] is not None
    if known != (value["p90"] is not None):
        raise PlanningError("invalid_forecast_snapshot")
    for key in ("p50", "p90"):
        if value[key] is None:
            continue
        _fields(value[key], {"date", "critical_path"})
        _time(value[key]["date"])
        path = value[key]["critical_path"]
        if (
            type(path) is not list
            or len(path) > MAX_UNITS
            or any(type(item) is not str or item not in units for item in path)
        ):
            raise PlanningError("invalid_forecast_snapshot")
    if known:
        if _time(value["p90"]["date"]) < _time(value["p50"]["date"]):
            raise PlanningError("estimate_range_reversed")
        if any(not row["committed"] and "p50_date" not in row for row in value["units"]):
            raise PlanningError("invalid_forecast_snapshot")
    remaining_key = "remaining_elapsed_hours" if "remaining_elapsed_hours" in value else "remaining_effort_hours"
    if remaining_key in value:
        _fields(value[remaining_key], {"p50", "p90"})
        if _number(value[remaining_key]["p90"]) < _number(value[remaining_key]["p50"]):
            raise PlanningError("estimate_range_reversed")
    if type(value["findings"]) is not list or any(type(row) is not dict for row in value["findings"]):
        raise PlanningError("invalid_forecast_snapshot")
    if type(value["model"]) is not str or len(value["model"]) > 4096:
        raise PlanningError("invalid_forecast_snapshot")


def _validate_plan(value: Any) -> None:
    _optional_fields(
        value,
        {
            "schema",
            "program_version",
            "done_tests",
            "scope_events",
            "change_days",
            "forecast_snapshot",
            "source_versions",
        },
        {"goal_records"},
    )
    if value["schema"] != SCHEMA:
        raise PlanningError("invalid_program_plan_section")
    _version(value["program_version"])
    if "goal_records" in value:
        goal_references(value["goal_records"])
    _validate_snapshot(value["forecast_snapshot"])
    versions = value["source_versions"]
    _fields(versions, {"projects", "tests", "specifications", "dependencies", "formal_links", "commit_links"})
    for family in ("projects", "tests", "specifications", "dependencies", "formal_links"):
        if type(versions[family]) is not dict:
            raise PlanningError("invalid_program_plan_section")
        for key, version in versions[family].items():
            _identifier(key)
            _version(version)
    if type(versions["commit_links"]) is not dict:
        raise PlanningError("invalid_program_plan_section")
    for key, row in versions["commit_links"].items():
        _identifier(key)
        _fields(row, {"version", "project_id", "artifact_ref"})
        _version(row["version"])
        if (
            type(row["project_id"]) is not str
            or row["project_id"] not in versions["projects"]
            or type(row["artifact_ref"]) is not str
        ):
            raise PlanningError("invalid_program_plan_section")
    if type(value["done_tests"]) is not dict or set(value["done_tests"]) != set(versions["projects"]):
        raise PlanningError("done_test_population_mismatch")
    selected_tests = {}
    for key, refs in value["done_tests"].items():
        _identifier(key)
        if type(refs) is not list or not 1 <= len(refs) <= 100:
            raise PlanningError("done_test_required")
        seen = set()
        for raw in refs:
            ref = _reference(raw)
            if ref["id"] in seen or selected_tests.get(ref["id"], ref["version"]) != ref["version"]:
                raise PlanningError("duplicate_planning_reference")
            seen.add(ref["id"])
            selected_tests[ref["id"]] = ref["version"]
    if selected_tests != versions["tests"]:
        raise PlanningError("invalid_program_plan_section")
    snapshot_projects = {row["project_id"]: row["project_version"] for row in value["forecast_snapshot"]["units"]}
    if snapshot_projects != versions["projects"]:
        raise PlanningError("invalid_program_plan_section")
    _fields(value["change_days"], set(CAUSES))
    for amount in value["change_days"].values():
        _number(amount, signed=True)
    if type(value["scope_events"]) is not list or len(value["scope_events"]) > 100:
        raise PlanningError("invalid_scope_event")
    for event in value["scope_events"]:
        _fields(event, {"cause", "direction", "effort_hours"})
        if (
            type(event["cause"]) is not str
            or not event["cause"].strip()
            or type(event["direction"]) is not str
            or event["direction"] not in {"added", "removed", "revised"}
        ):
            raise PlanningError("invalid_scope_event")
        _fields(event["effort_hours"], {"p50", "p90"})
        if _number(event["effort_hours"]["p90"]) < _number(event["effort_hours"]["p50"]):
            raise PlanningError("estimate_range_reversed")


def read_plan(notes: str | None) -> dict[str, Any] | None:
    if notes is None:
        return None
    if type(notes) is not str or len(notes) > 2000000:
        raise PlanningError("invalid_program_notes")
    if START not in notes and END not in notes:
        return None
    if notes.count(START) != 1 or notes.count(END) != 1 or notes.index(START) >= notes.index(END):
        raise PlanningError("ambiguous_program_plan_section")
    raw = notes[notes.index(START) + len(START) : notes.index(END)].strip()
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_unique,
            parse_float=_finite_json_number,
            parse_constant=lambda _: (_ for _ in ()).throw(PlanningError("invalid_planning_number")),
        )
    except (ValueError, TypeError, RecursionError):
        raise PlanningError("invalid_program_plan_section") from None
    _validate_plan(value)
    return value


def replace_plan(notes: str | None, value: dict[str, Any]) -> str:
    read_plan(notes)
    _validate_plan(value)
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).replace(
        "<", "\\u003c"
    )
    section = START + "\n" + raw + "\n" + END
    if notes is None:
        result = section
    elif START not in notes:
        result = notes + ("\n" if notes and not notes.endswith("\n") else "") + section
    else:
        result = notes[: notes.index(START)] + section + notes[notes.index(END) + len(END) :]
    if len(result) > 2000000:
        raise PlanningError("invalid_program_notes")
    return result


def child_fields(
    project: dict[str, Any] | None, program: dict[str, Any], *, rank: int, target_outcome: str, estimate: dict[str, Any]
) -> dict[str, Any]:
    """Fields for the existing project writer; no authorization/status mutation."""
    if program.get("kind") != "program" or program.get("status") != "active":
        raise PlanningError("active_program_required")
    _identifier(program["id"])
    if (
        type(rank) is not int
        or not 0 <= rank < 2147483647
        or type(target_outcome) is not str
        or not 1 <= len(target_outcome) <= 32768
    ):
        raise PlanningError("invalid_commit_unit")
    if project and (
        project.get("kind") != "project"
        or project.get("status") != "active"
        or project.get("parent_project_id") not in {None, program["id"]}
    ):
        raise PlanningError("project_parent_or_state_conflict")
    return {
        "parent_project_id": program["id"],
        "rank": rank,
        "target_outcome": target_outcome,
        "planning_estimate": validate_estimate(estimate),
    }


def dependency_fields(
    dependent: dict[str, Any], prerequisite: dict[str, Any], *, gate: str = "readiness", rationale: str
) -> dict[str, Any]:
    if (
        dependent.get("kind") != "project"
        or prerequisite.get("kind") != "project"
        or dependent["id"] == prerequisite["id"]
        or not dependent.get("parent_project_id")
        or dependent["parent_project_id"] != prerequisite.get("parent_project_id")
    ):
        raise PlanningError("invalid_unit_dependency")
    if gate not in {"readiness", "closure"} or type(rationale) is not str or not rationale.strip():
        raise PlanningError("invalid_unit_dependency")
    return {
        "dependent_project_id": dependent["id"],
        "prerequisite_project_id": prerequisite["id"],
        "dependency_kind": "requires_project_state",
        "required_prerequisite_state": "verified",
        "affected_gate": gate,
        "rationale": rationale,
        "status": "active",
    }


def _committed(project: dict[str, Any], links: list[dict[str, Any]]) -> bool:
    commits = [
        row
        for row in links
        if row.get("project_id") == project["id"]
        and row.get("artifact_type") == "git_commit"
        and row.get("relationship") == "activation"
        and row.get("status") == "active"
    ]
    return (
        project.get("status") == "verified"
        and bool(project.get("completed_at"))
        and len(commits) == 1
        and type(commits[0].get("artifact_ref")) is str
        and OID.fullmatch(commits[0]["artifact_ref"]) is not None
    )


def _done_tests(
    project: dict[str, Any],
    refs: list[dict[str, Any]],
    links: list[dict[str, Any]],
    tests: dict[str, dict[str, Any]],
    specs: dict[str, dict[str, Any]],
    selected_specs: dict[str, int],
) -> list[dict[str, Any]]:
    if type(refs) is not list or not 1 <= len(refs) <= 100:
        raise PlanningError("done_test_required")
    if type(project.get("application_scope")) is not str:
        raise PlanningError("done_test_scope_unclassified")
    roots = {
        row["artifact_ref"]
        for row in links
        if row.get("project_id") == project["id"]
        and row.get("artifact_type") == "spec"
        and row.get("status") == "active"
    }
    result, seen = [], set()
    for received in refs:
        ref = _reference(received)
        if ref["id"] in seen:
            raise PlanningError("duplicate_planning_reference")
        seen.add(ref["id"])
        test = tests.get(ref["id"])
        if (
            not test
            or test.get("id") != ref["id"]
            or test.get("version") != ref["version"]
            or test.get("application_scope") != project.get("application_scope")
        ):
            raise PlanningError("done_test_changed_or_out_of_scope")
        spec_id, ancestry = test.get("spec_id"), set()
        while spec_id:
            if spec_id in ancestry:
                raise PlanningError("formal_ancestry_cycle")
            ancestry.add(spec_id)
            spec = specs.get(spec_id)
            if (
                not spec
                or spec.get("id") != spec_id
                or spec.get("status") != "active"
                or spec.get("application_scope") != project.get("application_scope")
            ):
                raise PlanningError("done_test_formal_source_unavailable")
            selected_specs[spec_id] = _version(spec.get("version"))
            if spec_id in roots:
                break
            spec_id = spec.get("parent")
        else:
            raise PlanningError("done_test_not_linked_to_unit_formals")
        result.append(ref)
    return result


def _calendar(start: datetime, hours: float, pace: dict[str, Any]) -> str:
    # UTC calendar under an explicit observed elapsed pattern or the legacy
    # productive convention; the latter is not a measured work-shift schedule.
    remaining = hours / pace.get("productive_hours_per_day", pace.get("elapsed_hours_per_day"))
    if remaining > MAX_FORECAST_DAYS:
        raise PlanningError("forecast_horizon_exceeded")
    current = start
    if pace["days"] == "every_day":
        return (current + timedelta(days=remaining)).isoformat()
    if remaining == 0:
        return current.isoformat()
    while current.weekday() >= 5:
        current += timedelta(days=1)
    whole = int(remaining)
    for _ in range(whole):
        current += timedelta(days=1)
        while current.weekday() >= 5:
            current += timedelta(days=1)
    current += timedelta(days=remaining - whole)
    while current.weekday() >= 5:
        current += timedelta(days=1)
    return current.isoformat()


def forecast(
    program: dict[str, Any],
    children: list[dict[str, Any]],
    dependencies: list[dict[str, Any]],
    links: list[dict[str, Any]],
    *,
    as_of: str,
    pace: dict[str, Any],
    buffer_hours: dict[str, Any],
    pace_evidence: dict[tuple[str, str], dict[str, Any]] | None = None,
    estimate_evidence: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Critical-path ranges, preserving readiness versus closure semantics.

    Waiting uses wall hours. Duration and the terminal buffer use the selected
    elapsed pattern or legacy productive convention without relabelling inputs.
    Parallel capacity is not inferred from ranks or unlimited tokens.
    """
    start = _time(as_of)
    pace = _pace(pace, pace_evidence)
    _fields(buffer_hours, {"p50", "p90"})
    buffer = {key: _number(buffer_hours[key]) for key in ("p50", "p90")}
    if buffer["p90"] < buffer["p50"]:
        raise PlanningError("estimate_range_reversed")
    if program.get("kind") != "program" or len(children) > MAX_UNITS:
        raise PlanningError("invalid_program_population")
    rows = {}
    for row in children:
        if row.get("kind") != "project" or row.get("parent_project_id") != program["id"] or row["id"] in rows:
            raise PlanningError("invalid_program_population")
        _identifier(row["id"])
        _version(row["version"])
        if estimate_evidence is not None:
            validate_estimate(row.get("planning_estimate"), evidence=estimate_evidence)
        rows[row["id"]] = row
    ready, closure = {key: [] for key in rows}, {key: [] for key in rows}
    done = {key: _committed(row, links) for key, row in rows.items()}
    blockers = []
    for edge in dependencies:
        if edge.get("status") != "active" or edge.get("dependent_project_id") not in rows:
            continue
        dependent, prerequisite = edge["dependent_project_id"], edge.get("prerequisite_project_id")
        if done[dependent]:
            continue
        if edge.get("dependency_kind") != "requires_project_state" or edge.get("affected_gate") not in {
            "readiness",
            "closure",
        }:
            raise PlanningError("invalid_unit_dependency")
        if prerequisite not in rows:
            blockers.append(
                {
                    "unit": dependent,
                    "dependency": edge["id"],
                    "reason": "external_prerequisite_requires_current_readback",
                }
            )
            continue
        if edge.get("required_prerequisite_state") != "verified":
            if rows[prerequisite].get("status") != edge.get("required_prerequisite_state"):
                blockers.append(
                    {"unit": dependent, "dependency": edge["id"], "reason": "required_state_not_forecastable"}
                )
            continue
        (ready if edge["affected_gate"] == "readiness" else closure)[dependent].append(prerequisite)
    order, visiting, visited = [], set(), set()

    def visit(key):
        if key in visiting:
            raise PlanningError("unit_dependency_cycle")
        if key in visited:
            return
        visiting.add(key)
        for dep in sorted(set(ready[key] + closure[key])):
            visit(dep)
        visiting.remove(key)
        visited.add(key)
        order.append(key)

    for key in sorted(
        rows, key=lambda key: (rows[key].get("rank") if type(rows[key].get("rank")) is int else 2147483647, key)
    ):
        visit(key)
    estimates, unknown = {}, []
    duration_key = "elapsed_hours" if "elapsed_hours_per_day" in pace else "effort_hours"
    for key in order:
        if done[key]:
            continue
        estimate = validate_estimate(rows[key].get("planning_estimate"), evidence=estimate_evidence)
        if estimate is None:
            unknown.append({"unit": key, "reason": "estimate_unobserved"})
            continue
        if duration_key not in estimate or estimate[duration_key] is None:
            unknown.append({"unit": key, "reason": "duration_unobserved_or_unit_mismatch"})
            continue
        if any(row["p50_hours"] is None for row in estimate["waiting"]):
            unknown.append({"unit": key, "reason": "waiting_unknown"})
        estimates[key] = estimate
    units = [{"project_id": key, "project_version": rows[key]["version"], "committed": done[key]} for key in order]
    if unknown or blockers:
        return {
            "as_of": start.isoformat(),
            "pace": copy.deepcopy(pace),
            "buffer_hours": buffer,
            "p50": None,
            "p90": None,
            "units": units,
            "findings": unknown + blockers,
            "model": "Dependency bounds; parallel capacity and productive effort observations are not inferred.",
        }
    ranges = {}
    for quantile in ("p50", "p90"):
        finishes, paths = {}, {}
        for key in order:
            if done[key]:
                finishes[key], paths[key] = start, []
                continue
            if rows[key].get("status") != "active":
                raise PlanningError("unfinished_closed_unit")
            candidates = [(finishes[dep], paths[dep]) for dep in ready[key]] or [(start, [])]
            begin, path = max(candidates, key=lambda pair: (pair[0], pair[1]))
            estimate = estimates[key]
            waiting_hours = sum(row[quantile + "_hours"] for row in estimate["waiting"])
            if waiting_hours / 24 > MAX_FORECAST_DAYS:
                raise PlanningError("forecast_horizon_exceeded")
            own_finish = _time(_calendar(begin, estimate[duration_key][quantile], pace)) + timedelta(
                hours=waiting_hours
            )
            if (own_finish - start).total_seconds() / 86400 > MAX_FORECAST_DAYS:
                raise PlanningError("forecast_horizon_exceeded")
            causes = [(own_finish, [*path, key])] + [(finishes[dep], [*paths[dep], key]) for dep in closure[key]]
            finishes[key], paths[key] = max(causes, key=lambda pair: (pair[0], pair[1]))
            next(unit for unit in units if unit["project_id"] == key)[quantile + "_date"] = finishes[key].isoformat()
        finish, path = max(
            ((finishes[key], paths[key]) for key in order), default=(start, []), key=lambda pair: (pair[0], pair[1])
        )
        date = _calendar(finish, buffer[quantile], pace)
        if (_time(date) - start).total_seconds() / 86400 > MAX_FORECAST_DAYS:
            raise PlanningError("forecast_horizon_exceeded")
        ranges[quantile] = {"date": date, "critical_path": path}
    return {
        "as_of": start.isoformat(),
        "pace": copy.deepcopy(pace),
        "buffer_hours": buffer,
        **ranges,
        ("remaining_elapsed_hours" if duration_key == "elapsed_hours" else "remaining_effort_hours"): {
            key: sum(value[duration_key][key] for value in estimates.values()) for key in ("p50", "p90")
        },
        "units": units,
        "findings": [{"reason": "parallel_capacity_not_modelled"}],
        "model": (
            "Readiness waits before work; closure waits before finish. "
            "Elapsed duration under the cited operating pattern. One terminal buffer; "
            "dependency bounds are not statistical calibration."
            if duration_key == "elapsed_hours"
            else "Readiness waits before work; closure waits before finish. "
            "Productive effort is separate from wall-hour waiting. One terminal buffer."
        ),
    }


def revision_fields(
    program: dict[str, Any],
    children: list[dict[str, Any]],
    dependencies: list[dict[str, Any]],
    links: list[dict[str, Any]],
    tests: dict[str, dict[str, Any]],
    specifications: dict[str, dict[str, Any]],
    *,
    expected_version: int,
    target_outcome: str,
    done_tests: dict[str, list[dict[str, Any]]],
    scope_events: list[dict[str, Any]],
    change_days: dict[str, Any],
    as_of: str,
    pace: dict[str, Any],
    buffer_hours: dict[str, Any],
    goal_records: list[dict[str, Any]] | None = None,
    goal_record_observations: dict[tuple[str, str], dict[str, Any]] | None = None,
    pace_evidence: dict[tuple[str, str], dict[str, Any]] | None = None,
    estimate_evidence: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build one normal program-row revision, leaving unrelated notes intact."""
    if (
        program.get("version") != expected_version
        or program.get("kind") != "program"
        or program.get("status") != "active"
    ):
        raise PlanningError("program_version_or_state_conflict")
    _version(expected_version)
    if type(target_outcome) is not str or not 1 <= len(target_outcome) <= 32768:
        raise PlanningError("target_required")
    if (
        type(scope_events) is not list
        or len(scope_events) > 100
        or (target_outcome != program.get("target_outcome") and not scope_events)
    ):
        raise PlanningError("scope_event_required")
    normalized_events = []
    for event in scope_events:
        _fields(event, {"cause", "direction", "effort_hours"})
        if (
            type(event["cause"]) is not str
            or not event["cause"].strip()
            or type(event["direction"]) is not str
            or event["direction"] not in {"added", "removed", "revised"}
        ):
            raise PlanningError("invalid_scope_event")
        _fields(event["effort_hours"], {"p50", "p90"})
        low, high = _number(event["effort_hours"]["p50"]), _number(event["effort_hours"]["p90"])
        if high < low:
            raise PlanningError("estimate_range_reversed")
        normalized_events.append(
            {"cause": event["cause"], "direction": event["direction"], "effort_hours": {"p50": low, "p90": high}}
        )
    _fields(change_days, set(CAUSES))
    change = {key: _number(change_days[key], signed=True) for key in CAUSES}
    if set(done_tests) != {row["id"] for row in children}:
        raise PlanningError("done_test_population_mismatch")
    selected_specs: dict[str, int] = {}
    refs = {
        row["id"]: _done_tests(row, done_tests[row["id"]], links, tests, specifications, selected_specs)
        for row in children
    }
    snapshot = forecast(
        program,
        children,
        dependencies,
        links,
        as_of=as_of,
        pace=pace,
        buffer_hours=buffer_hours,
        pace_evidence=pace_evidence,
        estimate_evidence=estimate_evidence,
    )
    section = {
        "schema": SCHEMA,
        "program_version": expected_version + 1,
        "done_tests": refs,
        "scope_events": normalized_events,
        "change_days": change,
        "forecast_snapshot": snapshot,
        "source_versions": {
            "projects": {row["id"]: row["version"] for row in children},
            "tests": {ref["id"]: ref["version"] for rows in refs.values() for ref in rows},
            "specifications": selected_specs,
            "dependencies": {
                row["id"]: row["version"]
                for row in dependencies
                if row.get("status") == "active" and row.get("dependent_project_id") in refs
            },
            "commit_links": {
                row["id"]: {
                    "version": row["version"],
                    "project_id": row["project_id"],
                    "artifact_ref": row["artifact_ref"],
                }
                for row in links
                if row.get("status") == "active"
                and row.get("project_id") in refs
                and row.get("artifact_type") == "git_commit"
                and row.get("relationship") == "activation"
            },
            "formal_links": {
                row["id"]: row["version"]
                for row in links
                if row.get("status") == "active"
                and row.get("project_id") in refs
                and row.get("artifact_type") == "spec"
            },
        },
    }
    previous = read_plan(program.get("notes"))
    if goal_records is not None or (previous is not None and "goal_records" in previous):
        selected = goal_references(goal_records if goal_records is not None else previous["goal_records"])
        for ref in selected:
            row = (goal_record_observations or {}).get((ref["domain"], ref["id"]))
            if not row or row.get("id") != ref["id"] or row.get("version") != ref["version"]:
                raise PlanningError("goal_evidence_changed")
            if row.get("application_scope") != program.get("application_scope"):
                raise PlanningError("goal_evidence_out_of_scope")
        section["goal_records"] = selected
    return {
        "target_outcome": target_outcome,
        "notes": replace_plan(program.get("notes"), section),
        "target_date": None if snapshot["p50"] is None else _time(snapshot["p50"]["date"]).date().isoformat(),
    }


def forecast_change(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Checklist C; classification is declared canon data, not inferred causality."""
    if (
        previous.get("kind") != "program"
        or current.get("kind") != "program"
        or previous.get("id") != current.get("id")
        or _version(current.get("version")) <= _version(previous.get("version"))
    ):
        raise PlanningError("program_history_identity_or_order_conflict")
    _identifier(current.get("id"))
    before, after = read_plan(previous.get("notes")), read_plan(current.get("notes"))
    if before is None or after is None:
        return {"checklist": "C", "status": "unknown", "reason": "forecast_snapshot_unavailable", "canonical_writes": 0}
    if before["program_version"] != previous["version"] or after["program_version"] != current["version"]:
        return {
            "checklist": "C",
            "status": "unknown",
            "reason": "program_plan_snapshot_not_current_at_selected_revision",
            "canonical_writes": 0,
        }
    left, right = before["forecast_snapshot"].get("p50"), after["forecast_snapshot"].get("p50")
    if left is None or right is None:
        return {
            "checklist": "C",
            "status": "unknown",
            "reason": "forecast_contains_unknown_waiting_or_estimate",
            "canonical_writes": 0,
        }
    _fields(after["change_days"], set(CAUSES))
    parts = {key: _number(after["change_days"][key], signed=True) for key in CAUSES}
    total = (_time(right["date"]) - _time(left["date"])).total_seconds() / 86400
    remainder = total - sum(parts.values())
    return {
        "checklist": "C",
        "from_program_version": previous["version"],
        "to_program_version": current["version"],
        "target_changed": previous.get("target_outcome") != current.get("target_outcome"),
        "p50_date_change_days": total,
        "declared_change_days": parts,
        "unexplained_days": remainder,
        "findings": [] if abs(remainder) < 1e-9 else [{"reason": "unexplained_forecast_change", "days": remainder}],
        "classification_origin": (
            "Declared causes in the versioned program-plan section; no automatic causal attribution."
        ),
        "canonical_writes": 0,
    }


def _source_freshness(program, children, links, plan, *, tests, specifications, dependencies):
    versions = plan["source_versions"]
    findings = []
    if plan["program_version"] != program["version"]:
        findings.append({"family": "program", "reason": "version_changed"})
    current = {row["id"]: row for row in children}
    if len(current) != len(children) or set(current) != set(versions["projects"]):
        findings.append({"family": "projects", "reason": "population_changed"})
    for key, version in versions["projects"].items():
        row = current.get(key)
        if (
            not row
            or row.get("version") != version
            or row.get("parent_project_id") != program["id"]
            or row.get("kind") != "project"
        ):
            findings.append({"family": "projects", "id": key, "reason": "record_changed"})
    for family, records in (("tests", tests), ("specifications", specifications)):
        if records is None:
            findings.append({"family": family, "reason": "scoped_readback_unavailable"})
            continue
        for key, version in versions[family].items():
            row = records.get(key)
            if not row or row.get("id") != key or row.get("version") != version:
                findings.append({"family": family, "id": key, "reason": "record_changed"})
    actual_formals = {
        row["id"]: row["version"]
        for row in links
        if row.get("status") == "active" and row.get("project_id") in current and row.get("artifact_type") == "spec"
    }
    actual_commits = {
        row["id"]: {"version": row["version"], "project_id": row["project_id"], "artifact_ref": row["artifact_ref"]}
        for row in links
        if row.get("status") == "active"
        and row.get("project_id") in current
        and row.get("artifact_type") == "git_commit"
        and row.get("relationship") == "activation"
    }
    if actual_formals != versions["formal_links"]:
        findings.append({"family": "formal_links", "reason": "population_or_version_changed"})
    if actual_commits != versions["commit_links"]:
        findings.append({"family": "commit_links", "reason": "population_version_or_reference_changed"})
    if dependencies is None:
        findings.append({"family": "dependencies", "reason": "scoped_readback_unavailable"})
    else:
        actual = {
            row["id"]: row["version"]
            for row in dependencies
            if row.get("status") == "active" and row.get("dependent_project_id") in current
        }
        if actual != versions["dependencies"]:
            findings.append({"family": "dependencies", "reason": "population_or_version_changed"})
    stale = any(row["reason"] != "scoped_readback_unavailable" for row in findings)
    unknown = any(row["reason"] == "scoped_readback_unavailable" for row in findings)
    return {"status": "stale" if stale else "unknown" if unknown else "current", "findings": findings}


def goal_references(value: Any) -> list[dict[str, Any]]:
    """Validate exact scoped record references without interpreting acceptance."""
    if type(value) is not list or len(value) > 100:
        raise PlanningError("invalid_goal_evidence")
    seen = set()
    for ref in value:
        _fields(ref, {"domain", "id", "version"})
        if type(ref["domain"]) is not str or ref["domain"] not in {"tests", "work-items", "projects"}:
            raise PlanningError("invalid_goal_evidence")
        key = ref["domain"], _identifier(ref["id"])
        _version(ref["version"])
        if key in seen:
            raise PlanningError("duplicate_planning_reference")
        seen.add(key)
    return copy.deepcopy(value)


def goal_observations(program, plan, records, links, *, as_of):
    """Read actual normal endings; never issue an independent verdict or status."""
    now = _time(as_of)
    observations = []
    for ref in goal_references(plan.get("goal_records", [])):
        row = (records or {}).get((ref["domain"], ref["id"]))
        result = {**ref, "status": "unobserved", "normal_ending_observed": None}
        if row is not None:
            if row.get("id") != ref["id"] or row.get("version") != ref["version"]:
                result["status"] = "stale"
            elif row.get("application_scope") != program.get("application_scope"):
                result["status"] = "out_of_scope"
            else:
                result["status"] = "current"
                if ref["domain"] == "tests":
                    result.update(
                        {
                            key: copy.deepcopy(row.get(key))
                            for key in ("last_result", "last_executed_at", "last_executed_on", "execution_evidence")
                        }
                    )
                    executed = row.get("last_executed_at")
                    result["normal_ending_observed"] = bool(
                        row.get("last_result") == "pass"
                        and executed
                        and _time(str(executed)) <= now
                        and row.get("execution_evidence")
                    )
                elif ref["domain"] == "work-items":
                    result.update(
                        {
                            key: copy.deepcopy(row.get(key))
                            for key in ("resolution_status", "completion_evidence", "status_detail")
                        }
                    )
                    result["normal_ending_observed"] = row.get("resolution_status") in {"verified", "retired"}
                else:
                    result.update({key: copy.deepcopy(row.get(key)) for key in ("status", "completed_at")})
                    result["normal_ending_observed"] = True if _committed(row, links) else None
        observations.append(result)
    return {
        "records": observations,
        "independent_goal_verification": "unobserved_by_planning",
        "goal_achieved": None,
        "program_commit_required": False,
        "reason": "Normal record endings are observations, not a goal-level independent verdict.",
    }


def forecast_report(
    program,
    children,
    dependencies,
    links,
    *,
    as_of,
    tests=None,
    specifications=None,
    goal_record_observations=None,
    pace_evidence=None,
    estimate_evidence=None,
):
    """Complete current read-only graph, provenance and unknowns; no calibration."""
    plan = read_plan(program.get("notes"))
    if plan is None:
        raise PlanningError("program_plan_required")
    previous = plan["forecast_snapshot"]
    result = forecast(
        program,
        children,
        dependencies,
        links,
        as_of=as_of,
        pace=previous["pace"],
        buffer_hours=previous["buffer_hours"],
        pace_evidence=pace_evidence,
        estimate_evidence=estimate_evidence,
    )
    nodes = []
    for row in children:
        nodes.append(
            {
                "project_id": row["id"],
                "project_version": row["version"],
                "remaining": not _committed(row, links),
                "target_outcome": row.get("target_outcome"),
                "planning_estimate": validate_estimate(row.get("planning_estimate"), evidence=estimate_evidence),
                "success_probability": (row.get("planning_estimate") or {}).get("success_probability"),
            }
        )
    return {
        "program_id": program["id"],
        "program_version": program["version"],
        "as_of": _time(as_of).isoformat(),
        "forecast": result,
        "remaining_graph": {
            "nodes": nodes,
            "dependencies": copy.deepcopy(dependencies),
            "goal_records": copy.deepcopy(plan.get("goal_records", [])),
        },
        "source_freshness": _source_freshness(
            program,
            children,
            links,
            plan,
            tests=tests,
            specifications=specifications,
            dependencies=dependencies,
        ),
        "goal_evidence": goal_observations(program, plan, goal_record_observations, links, as_of=as_of),
        "goal_success_probability": None,
        "statistical_calibration": "not_established",
        "reason": "Unit probabilities are declared inputs; no joint distribution or independence is inferred.",
        "canonical_writes": 0,
    }


def checkpoint(
    program: dict[str, Any],
    children: list[dict[str, Any]],
    links: list[dict[str, Any]],
    *,
    as_of: str,
    previous_snapshot: dict[str, Any] | None = None,
    attempts: list[dict[str, Any]] | None = None,
    tests: dict[str, dict[str, Any]] | None = None,
    specifications: dict[str, dict[str, Any]] | None = None,
    dependencies: list[dict[str, Any]] | None = None,
    goal_record_observations: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Checklist B from canonical observations. Missing evidence stays unknown."""
    now = _time(as_of)
    plan = read_plan(program.get("notes"))
    if plan is None:
        raise PlanningError("program_plan_required")
    last = previous_snapshot if previous_snapshot is not None else plan["forecast_snapshot"]
    _validate_snapshot(last)
    freshness = _source_freshness(
        program, children, links, plan, tests=tests, specifications=specifications, dependencies=dependencies
    )
    plan_current = {"current": True, "stale": False, "unknown": None}[freshness["status"]]
    current = {row["id"]: row for row in children}
    measures, stale = [], []
    for planned in last["units"]:
        row = current.get(planned["project_id"])
        if not row or row.get("parent_project_id") != program["id"]:
            stale.append(planned["project_id"])
            continue
        if row["version"] != planned["project_version"]:
            stale.append(row["id"])
        done = _committed(row, links)
        completed = _time(str(row["completed_at"])) if done else None
        p50, p90 = planned.get("p50_date"), planned.get("p90_date")
        slip = ((completed or now) - _time(p50)).total_seconds() / 86400 if p50 else None
        measures.append(
            {
                "project_id": row["id"],
                "due_under_last_revision": _time(p50) <= now if p50 else None,
                "done_by_native_commit": done,
                "done_test_refs": plan["done_tests"].get(row["id"], []),
                "test_execution": "unobserved_by_project_commit_report",
                "p50_slip_days": slip,
                "p90_exceeded": (completed or now) > _time(p90) if p90 else None,
            }
        )
    observed_wip = (
        None
        if attempts is None
        else sorted(
            {
                row["project_id"]
                for row in attempts
                if row.get("project_id") in current
                and row.get("disposition") == "active"
                and not _committed(current[row["project_id"]], links)
            }
        )
    )
    current_snapshot = plan["forecast_snapshot"]
    path_changed = None
    if (
        previous_snapshot is not None
        and plan_current is True
        and not stale
        and all(last.get(key) is not None and current_snapshot.get(key) is not None for key in ("p50", "p90"))
    ):
        path_changed = any(
            last[key]["critical_path"] != current_snapshot[key]["critical_path"] for key in ("p50", "p90")
        )
    previous_time = _time(last["as_of"])
    commits = [
        row["id"]
        for row in children
        if _committed(row, links) and previous_time < _time(str(row["completed_at"])) <= now
    ]
    report = {
        "checklist": "B",
        "program_id": program["id"],
        "program_version": program["version"],
        "plan_program_version": plan["program_version"],
        "plan_is_current": plan_current,
        "source_freshness": freshness,
        "as_of": now.isoformat(),
        "B1_B2_milestones": measures,
        "B3_effort_spent_and_remaining_fall": {
            "status": "unknown",
            "reason": "productive_effort_not_observed; elapsed history is not effort",
        },
        "B4_critical_path_changed": {
            "changed": path_changed,
            "reason": "Requires two current canonical forecast snapshots; stale source versions remain unknown.",
        },
        "B5_work_in_progress": {
            "count": None if observed_wip is None else len(observed_wip),
            "project_ids": observed_wip,
            "basis": "Current active canonical bridge attempts; scheduled start_date is not an observation of work.",
        },
        "B6_blocker_and_owner_queue_age": {
            "status": "unknown",
            "reason": "queue/blocker creation observations not present in project records",
        },
        "B7_output": {
            "native_project_commits_since_snapshot": commits,
            "release_rows": "unobserved",
            "workloads": "unobserved",
        },
        "B8_document_growth": {
            "status": "unknown",
            "reason": "external file measurements are not canonical project observations",
        },
        "stale_source_project_ids": sorted(set(stale)),
        "canonical_writes": 0,
    }
    if "goal_records" in plan:
        report["goal_evidence"] = goal_observations(program, plan, goal_record_observations, links, as_of=as_of)
    return report
