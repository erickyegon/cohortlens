"""Cohort definition model (docs/spec.md FR-3, FR-4, FR-5; docs/cohort-engine.md).

A definition is pure data validated here, before anything else runs. This module deliberately imports no database
driver, so validating a definition can never execute SQL. Errors are reported field by field, all at once, as
`DefinitionError.errors == [{"field": "rules.1.window.end_days", "message": "..."}]`; messages never echo
submitted text (names, labels), only the dotted path of the offending field.

Windows are day offsets relative to the index date: negative is before, `0` is the index date itself.
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

MAX_RULES = 10
MAX_RULES_ALL_EVENTS = 3  # rule cap when first_occurrence_only is false (every exposure is an index row, #37)
MAX_AGE = 130
MAX_CONCEPT_ID = 2_147_483_647  # OMOP concept_id is a Postgres integer
MIN_DATE = dt.date(1900, 1, 1)
MAX_DATE = dt.date(2100, 12, 31)
MAX_WINDOW_DAYS = 36_500  # 100 years: wide enough for "any time before" on any real patient history
ANY_TIME_BEFORE = -MAX_WINDOW_DAYS

def _text_only(value: Any) -> Any:
    """Enumerated fields accept real text only: pydantic's lax mode would turn bytes into text."""
    if not isinstance(value, str):
        raise ValueError("must be text")
    return value


TextOnly = BeforeValidator(_text_only)
# measurement criteria need value ranges and arrive in a later issue
Domain = Annotated[Literal["condition", "drug"], TextOnly]

ConceptId = Annotated[int, Field(strict=True, gt=0, le=MAX_CONCEPT_ID)]
ConceptIds = Annotated[list[ConceptId], Field(min_length=1, max_length=1000)]
Days = Annotated[int, Field(strict=True, ge=-MAX_WINDOW_DAYS, le=MAX_WINDOW_DAYS)]
NonNegativeDays = Annotated[int, Field(strict=True, ge=0, le=MAX_WINDOW_DAYS)]
Age = Annotated[int, Field(strict=True, ge=0, le=MAX_AGE)]
Flag = Annotated[bool, Field(strict=True)]  # "yes"/"on"/1 must not silently flip a criterion


def _printable_text(value: str) -> str:
    """NFC-normalise and reject control, format (zero-width, bidi) and non-space separator characters.

    Names and labels flow into logs, spans, run history and the UI, so they must be plain visible text.
    """
    value = unicodedata.normalize("NFC", value).strip()
    for ch in value:
        category = unicodedata.category(ch)
        if category[0] == "C" or (category[0] == "Z" and ch != " "):
            raise ValueError("must contain only printable characters")
    if not value:
        raise ValueError("must not be empty")
    return value


Label = Annotated[str, Field(strict=True, min_length=1, max_length=100), AfterValidator(_printable_text)]
Name = Annotated[str, Field(strict=True, min_length=1, max_length=200), AfterValidator(_printable_text)]

_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")  # ASCII digits only (\d also matches other scripts)


def _iso_date(value: Any) -> Any:
    """Accept only ISO `YYYY-MM-DD` strings (or date objects): no timestamps, no datetimes, no other formats."""
    if isinstance(value, str) and not _ISO_DATE.fullmatch(value):
        raise ValueError("must be a date in YYYY-MM-DD format")
    if isinstance(value, dt.datetime) or (not isinstance(value, (str, dt.date)) and value is not None):
        raise ValueError("must be a date in YYYY-MM-DD format")
    return value


def _plausible_date(value: dt.date | None) -> dt.date | None:
    if value is not None and not MIN_DATE <= value <= MAX_DATE:
        raise ValueError(f"must be between {MIN_DATE} and {MAX_DATE}")
    return value


IsoDate = Annotated[dt.date | None, BeforeValidator(_iso_date), AfterValidator(_plausible_date)]


def _unique_in_order(values: list) -> list:
    return list(dict.fromkeys(values))


class _Strict(BaseModel):
    # an unknown key is an error: a typo must never silently drop a criterion
    model_config = ConfigDict(extra="forbid")


class DateRange(_Strict):
    start: IsoDate = None
    end: IsoDate = None

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
    include_descendants: Flag = True
    first_occurrence_only: Flag = True
    date_range: DateRange | None = None

    @field_validator("concept_ids")
    @classmethod
    def unique_concepts(cls, values):
        return _unique_in_order(values)


class Demographics(_Strict):
    age_min: Age | None = None
    age_max: Age | None = None
    sex: Annotated[list[Annotated[Literal["F", "M"], TextOnly]], Field(min_length=1)] = Field(
        default_factory=lambda: ["F", "M"]
    )

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
    type: Annotated[Literal["include", "exclude"], TextOnly]
    domain: Domain
    concept_ids: ConceptIds
    include_descendants: Flag = True
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

    @field_validator("rules")
    @classmethod
    def labels_are_unique(cls, rules):
        seen: dict[str, int] = {}
        for position, rule in enumerate(rules):
            key = rule.label.casefold()
            if key in seen:
                raise ValueError(f"rule labels must be unique ignoring case (rules {seen[key]} and {position})")
            seen[key] = position
        return rules


class DefinitionError(Exception):
    """A definition failed validation. `errors` is a list of `{"field": dotted.path, "message": text}`."""

    def __init__(self, errors: list[dict[str, str]]):
        self.errors = errors
        super().__init__("; ".join(f"{e['field'] or '<definition>'}: {e['message']}" for e in errors))


_FIELD_NAMES = {name for model in (DateRange, IndexEvent, Demographics, Observation, Window, Rule, CohortDefinition)
                for name in model.model_fields}


_LIST_FIELDS = {"rules", "concept_ids", "sex"}


def _path(loc: tuple, prefix: tuple = ()) -> str:
    """Build a dotted path from an error location. Only declared field names and list positions are kept:
    a dict key typed by the user (an unknown or non-string key) is replaced, never echoed."""
    parts: list[str] = []
    previous = None
    for part in (*prefix, *loc):
        if isinstance(part, int) and previous in _LIST_FIELDS:
            parts.append(str(part))
        elif isinstance(part, str) and part in _FIELD_NAMES:
            parts.append(part)
        else:
            parts.append("<unknown field>")
        previous = part
    return ".".join(parts)


def _field_errors(exc: ValidationError, prefix: tuple = ()) -> list[dict[str, str]]:
    errors = []
    for err in exc.errors(include_input=False, include_url=False, include_context=False):
        message = err["msg"].removeprefix("Value error, ")
        errors.append({"field": _path(err["loc"], prefix), "message": message})
    return errors


def parse_definition(raw: Any) -> CohortDefinition:
    """Validate raw JSON data (a dict) and return the typed definition, or raise `DefinitionError`.

    Besides the field rules this applies the cost cap for definitions where every exposure is an index event
    (`_all_events_errors`). `from None` is deliberate: the chained pydantic error carries the submitted input values.
    """
    if not isinstance(raw, dict):
        raise DefinitionError([{"field": "", "message": "A cohort definition must be a JSON object"}])
    rules = raw.get("rules")
    definition: CohortDefinition | None = None
    if isinstance(rules, list) and len(rules) > MAX_RULES:
        errors = _errors_for_too_many_rules(raw, rules)
    else:
        try:
            definition, errors = CohortDefinition.model_validate(raw), []
        except ValidationError as exc:
            errors = _field_errors(exc)
    reported = {e["field"] for e in errors}
    errors += [e for e in _all_events_errors(raw) if e["field"] not in reported]
    if errors:
        raise DefinitionError(errors)
    assert definition is not None
    return definition


def _all_events_errors(raw: dict) -> list[dict[str, str]]:
    """Cost cap for `first_occurrence_only: false` (every matching record becomes an index row, and each rule is probed
    once per row, so many rules or occurrence counts can outrun the 10 s statement timeout): at most
    `MAX_RULES_ALL_EVENTS` rules, each with `min_occurrences` 1. Works on the raw data so that these errors arrive in
    the same round as the other field errors; a non-boolean flag is left to the strict type check, and messages never
    echo submitted values.
    """
    index = raw.get("index_event")
    if not isinstance(index, dict) or index.get("first_occurrence_only") is not False:
        return []
    rules = raw.get("rules")
    if not isinstance(rules, list):
        return []
    errors = []
    if len(rules) > MAX_RULES_ALL_EVENTS:
        errors.append({"field": "rules", "message": (
            f"With first_occurrence_only false a cohort can have at most {MAX_RULES_ALL_EVENTS} rules")})
    for position, rule in enumerate(rules):
        occurrences = rule.get("min_occurrences") if isinstance(rule, dict) else None
        if isinstance(occurrences, int) and not isinstance(occurrences, bool) and occurrences != 1:
            errors.append({"field": f"rules.{position}.min_occurrences", "message": (
                "With first_occurrence_only false every rule must have min_occurrences 1")})
    return errors


def _errors_for_too_many_rules(raw: dict, rules: list) -> list[dict[str, str]]:
    """Pydantic stops at the list-length check without looking at the items; validate everything else and each
    rule separately so that fixing the count does not reveal a second round of errors."""
    errors = [{"field": "rules", "message": f"A cohort can have at most {MAX_RULES} rules"}]
    try:
        CohortDefinition.model_validate({**raw, "rules": []})
    except ValidationError as exc:
        errors += _field_errors(exc)
    for position, rule in enumerate(rules):
        try:
            Rule.model_validate(rule)
        except ValidationError as exc:
            errors += _field_errors(exc, prefix=("rules", position))
    return errors
