"""Cohort definition model (docs/spec.md FR-3, FR-4, FR-5; docs/cohort-engine.md).

A definition is pure data validated here, before anything else runs. This module deliberately imports no database
driver, so validating a definition can never execute SQL. Errors are reported field by field, all at once, as
`DefinitionError.errors == [{"field": "rules.1.window.end_days", "message": "..."}]`; messages never echo
submitted text (names, labels), only the dotted path of the offending field.

Windows are day offsets relative to the index date: negative is before, `0` is the index date itself.
"""
from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator

MAX_RULES = 10
MAX_AGE = 130
MAX_WINDOW_DAYS = 36_500  # 100 years: wide enough for "any time before" on any real patient history
ANY_TIME_BEFORE = -MAX_WINDOW_DAYS

Domain = Literal["condition", "drug"]  # measurement criteria need value ranges and arrive in a later issue

ConceptId = Annotated[int, Field(strict=True, gt=0)]
ConceptIds = Annotated[list[ConceptId], Field(min_length=1, max_length=1000)]
Days = Annotated[int, Field(strict=True, ge=-MAX_WINDOW_DAYS, le=MAX_WINDOW_DAYS)]
NonNegativeDays = Annotated[int, Field(strict=True, ge=0, le=MAX_WINDOW_DAYS)]
Age = Annotated[int, Field(strict=True, ge=0, le=MAX_AGE)]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


def _unique_in_order(values: list) -> list:
    return list(dict.fromkeys(values))


class _Strict(BaseModel):
    # an unknown key is an error: a typo must never silently drop a criterion
    model_config = ConfigDict(extra="forbid")


class DateRange(_Strict):
    start: dt.date | None = None
    end: dt.date | None = None

    @field_validator("end")
    @classmethod
    def end_not_before_start(cls, end, info):
        start = info.data.get("start")
        if end is not None and start is not None and end < start:
            raise ValueError("end must not be before start")
        return end


class IndexEvent(_Strict):
    domain: Domain
    concept_ids: ConceptIds
    include_descendants: bool = True
    first_occurrence_only: bool = True
    date_range: DateRange | None = None

    @field_validator("concept_ids")
    @classmethod
    def unique_concepts(cls, values):
        return _unique_in_order(values)


class Demographics(_Strict):
    age_min: Age | None = None
    age_max: Age | None = None
    sex: Annotated[list[Literal["F", "M"]], Field(min_length=1)] = Field(default_factory=lambda: ["F", "M"])

    @field_validator("age_max")
    @classmethod
    def age_min_not_above_age_max(cls, age_max, info):
        age_min = info.data.get("age_min")
        if age_max is not None and age_min is not None and age_min > age_max:
            raise ValueError(f"age_min ({age_min}) must not be greater than age_max ({age_max})")
        return age_max

    @field_validator("sex")
    @classmethod
    def unique_sex(cls, values):
        return _unique_in_order(values)


class Observation(_Strict):
    prior_days: NonNegativeDays = 0
    post_days: NonNegativeDays = 0


class Window(_Strict):
    start_days: Days
    end_days: Days

    @field_validator("end_days")
    @classmethod
    def start_not_after_end(cls, end_days, info):
        start_days = info.data.get("start_days")
        if start_days is not None and start_days > end_days:
            raise ValueError(f"start_days ({start_days}) must not be after end_days ({end_days})")
        return end_days


class Rule(_Strict):
    label: Label
    type: Literal["include", "exclude"]
    domain: Domain
    concept_ids: ConceptIds
    include_descendants: bool = True
    window: Window
    min_occurrences: Annotated[int, Field(strict=True, ge=1, le=100)] = 1

    @field_validator("concept_ids")
    @classmethod
    def unique_concepts(cls, values):
        return _unique_in_order(values)


class CohortDefinition(_Strict):
    name: Name
    index_event: IndexEvent
    demographics: Demographics = Field(default_factory=Demographics)
    observation: Observation = Field(default_factory=Observation)
    rules: Annotated[list[Rule], Field(max_length=MAX_RULES)] = Field(default_factory=list)


class DefinitionError(Exception):
    """A definition failed validation. `errors` is a list of `{"field": dotted.path, "message": text}`."""

    def __init__(self, errors: list[dict[str, str]]):
        self.errors = errors
        super().__init__("; ".join(f"{e['field'] or '<definition>'}: {e['message']}" for e in errors))


def _field_errors(exc: ValidationError) -> list[dict[str, str]]:
    errors = []
    for err in exc.errors(include_input=False, include_url=False, include_context=False):
        message = err["msg"].removeprefix("Value error, ")
        errors.append({"field": ".".join(str(part) for part in err["loc"]), "message": message})
    return errors


def parse_definition(raw: Any) -> CohortDefinition:
    """Validate raw JSON data (a dict) and return the typed definition, or raise `DefinitionError`."""
    if not isinstance(raw, dict):
        raise DefinitionError([{"field": "", "message": "A cohort definition must be a JSON object"}])
    try:
        return CohortDefinition.model_validate(raw)
    except ValidationError as exc:
        raise DefinitionError(_field_errors(exc)) from None
