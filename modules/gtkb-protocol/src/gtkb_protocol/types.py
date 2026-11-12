"""Strict wire envelopes; native authority and business validation stay in AUTH."""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal, TypeAlias, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .codec import canonical_json_bytes

WireJSON: TypeAlias = Union[None, bool, int, float, Decimal, str, list["WireJSON"], dict[str, "WireJSON"]]
Identifier = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")]
FormalLinkIdentifier = Annotated[str, Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")]
Text = Annotated[str, Field(min_length=1)]
Version = Annotated[int, Field(ge=0, lt=2_147_483_647)]
Scope = Annotated[str, Field(pattern=r"^(gtkb_platform|application:[A-Za-z][A-Za-z0-9_-]*)$")]
RecordDomain: TypeAlias = Literal["terms", "specifications", "tests", "test-plans", "test-phases", "harnesses"]


class _WireModel(BaseModel):
    def model_dump(self, *, mode="python", exclude_unset=False, **kwargs):
        # The wire path must not impose Pydantic's generic Any serialization depth
        # or narrow Decimal/huge integer values before the canonical codec sees them.
        if mode != "python" or kwargs:
            return super().model_dump(mode=mode, exclude_unset=exclude_unset, **kwargs)
        result = {}
        for name in type(self).model_fields:
            if exclude_unset and name not in self.model_fields_set:
                continue
            value = getattr(self, name)
            if isinstance(value, _WireModel):
                value = value.model_dump(mode="python", exclude_unset=exclude_unset)
            elif isinstance(value, list):
                value = [item.model_dump(mode="python", exclude_unset=exclude_unset)
                         if isinstance(item, _WireModel) else item for item in value]
            result[name] = value
        result.update(self.__pydantic_extra__ or {})
        return result


class Request(_WireModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Mutation(Request):
    expected_version: Version
    actor: Text
    reason: Text
    native_context_id: Text | None = None


class SessionRequest(Request):
    native_context_id: Text


class ClaimRequest(SessionRequest):
    work_item_id: Identifier | None = None
    expected_version: int = Field(ge=0)
    intended_status: Text
    request_id: Identifier


class FenceRequest(SessionRequest):
    fence: int = Field(ge=1)


class RecordMutation(Mutation):
    fields: dict[str, Any]

    @model_validator(mode="after")
    def _wire_fields(self):
        canonical_json_bytes(self.fields)
        return self


class _Response(_WireModel):
    model_config = ConfigDict(extra="allow", strict=True, hide_input_in_errors=True)

    @model_validator(mode="after")
    def _wire_graph(self):
        canonical_json_bytes(self.model_dump(mode="python"))
        return self


class Record(_Response):
    id: Annotated[str, Field(min_length=1)]
    version: Annotated[int, Field(ge=1)]


class HistoryEntry(_Response):
    version: Annotated[int, Field(ge=1)]
    prior_version: Annotated[int, Field(ge=1)] | None
    actor: str
    changed_at: str
    reason: str
    state: Record


class RecordHistory(_Response):
    current: Record
    history: list[HistoryEntry]


class RecordPage(_Response):
    records: list[Record]
    next_after: Annotated[str, Field(min_length=1)] | None
