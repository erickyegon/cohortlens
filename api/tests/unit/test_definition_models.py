"""Issue #5: cohort definition model and validation (docs/spec.md FR-3, FR-4, FR-5; docs/cohort-engine.md).

Impossible definitions are rejected before any SQL is built. Errors are field-level: each one names the dotted path
of the offending field (for example `demographics.age_max` or `rules.0.window.end_days`) so the UI can mark it.
"""
import copy
import json
import pathlib
import subprocess
import sys

import pytest
from app.cohort_engine.models import (
    ANY_TIME_BEFORE,
    MAX_RULES,
    CohortDefinition,
    DefinitionError,
    parse_definition,
)

API_DIR = pathlib.Path(__file__).resolve().parents[2]
REFERENCE = json.loads((API_DIR / "tests" / "fixtures" / "reference_cohort.json").read_text(encoding="utf-8"))


def reference():
    return copy.deepcopy(REFERENCE)


def rule(**overrides):
    base = {"label": "Heart failure", "type": "exclude", "domain": "condition", "concept_ids": [316139],
            "window": {"start_days": -365, "end_days": -1}}
    base.update(overrides)
    return base


def fields(exc_info):
    return {e["field"]: e["message"] for e in exc_info.value.errors}


# ---- valid definitions --------------------------------------------------------------------------------------

def test_reference_cohort_from_the_spec_is_expressible():
    definition = parse_definition(reference())
    assert isinstance(definition, CohortDefinition)
    assert definition.index_event.concept_ids == [1308216]
    assert [r.type for r in definition.rules] == ["exclude", "include", "exclude"]
    assert definition.rules[1].window.start_days == ANY_TIME_BEFORE
    assert definition.demographics.age_min == 50 and definition.demographics.age_max is None


def test_definition_round_trips_through_json():
    definition = parse_definition(reference())
    again = parse_definition(json.loads(definition.model_dump_json()))
    assert again == definition


def test_defaults_are_the_safe_choice():
    minimal = {"name": "x", "index_event": {"domain": "condition", "concept_ids": [320128]}}
    definition = parse_definition(minimal)
    assert definition.index_event.include_descendants is True
    assert definition.index_event.first_occurrence_only is True
    assert definition.demographics.sex == ["F", "M"]
    assert definition.observation.prior_days == 0 and definition.observation.post_days == 0
    assert definition.rules == []


def test_rule_defaults():
    definition = parse_definition({**reference(), "rules": [rule()]})
    assert definition.rules[0].include_descendants is True
    assert definition.rules[0].min_occurrences == 1


def test_duplicate_concept_ids_are_collapsed_in_order():
    data = reference()
    data["index_event"]["concept_ids"] = [3, 1, 3, 2, 1]
    assert parse_definition(data).index_event.concept_ids == [3, 1, 2]


def test_ten_rules_are_allowed_and_eleven_are_not():
    assert MAX_RULES == 10
    ok = {**reference(), "rules": [rule(label=f"r{i}") for i in range(10)]}
    assert len(parse_definition(ok).rules) == 10


def test_equal_window_bounds_and_equal_age_bounds_are_valid():
    data = reference()
    data["demographics"] = {"age_min": 65, "age_max": 65}
    data["rules"] = [rule(window={"start_days": 0, "end_days": 0})]
    parse_definition(data)


# ---- issue #5 acceptance criteria ---------------------------------------------------------------------------

def test_age_min_above_age_max_names_the_age_fields():
    data = reference()
    data["demographics"] = {"age_min": 80, "age_max": 40}
    with pytest.raises(DefinitionError) as exc:
        parse_definition(data)
    errors = fields(exc)
    assert "demographics.age_max" in errors
    assert "age_min" in errors["demographics.age_max"] and "age_max" in errors["demographics.age_max"]


def test_rule_with_empty_concept_list_fails_with_a_field_level_error():
    data = reference()
    data["rules"] = [rule(concept_ids=[])]
    with pytest.raises(DefinitionError) as exc:
        parse_definition(data)
    assert "rules.0.concept_ids" in fields(exc)


def test_index_event_with_empty_concept_list_fails():
    data = reference()
    data["index_event"]["concept_ids"] = []
    with pytest.raises(DefinitionError) as exc:
        parse_definition(data)
    assert "index_event.concept_ids" in fields(exc)


def test_window_start_after_end_fails():
    data = reference()
    data["rules"] = [rule(), rule(label="second", window={"start_days": 10, "end_days": 5})]
    with pytest.raises(DefinitionError) as exc:
        parse_definition(data)
    errors = fields(exc)
    assert "rules.1.window.end_days" in errors
    assert "start_days" in errors["rules.1.window.end_days"]
    assert "rules.0.window.end_days" not in errors


def test_eleven_rules_fail():
    data = {**reference(), "rules": [rule(label=f"r{i}") for i in range(11)]}
    with pytest.raises(DefinitionError) as exc:
        parse_definition(data)
    assert "rules" in fields(exc)
    assert "10" in fields(exc)["rules"]


def test_validation_has_no_database_dependency_so_no_sql_can_run():
    """Validation must be import-only pure Python: importing the model module never loads a database driver."""
    code = ("import sys; import app.cohort_engine.models; "
            "bad = [m for m in ('sqlalchemy', 'psycopg', 'psycopg2') if m in sys.modules]; "
            "sys.exit(1 if bad else 0)")
    result = subprocess.run([sys.executable, "-c", code], cwd=API_DIR, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


# ---- more impossible definitions ----------------------------------------------------------------------------

@pytest.mark.parametrize("age_field,value", [("age_min", -1), ("age_max", 131), ("age_min", 1.5)])
def test_age_bounds_must_be_sensible_integers(age_field, value):
    data = reference()
    data["demographics"] = {age_field: value}
    with pytest.raises(DefinitionError) as exc:
        parse_definition(data)
    assert f"demographics.{age_field}" in fields(exc)


def test_sex_list_must_be_non_empty_and_known():
    for sex in ([], ["X"], ["F", "F", "Q"]):
        data = reference()
        data["demographics"] = {"sex": sex}
        with pytest.raises(DefinitionError) as exc:
            parse_definition(data)
        assert any(f.startswith("demographics.sex") for f in fields(exc)), sex


def test_concept_ids_must_be_positive_integers():
    for bad in ([0], [-5], ["abc"], [1.5]):
        data = reference()
        data["index_event"]["concept_ids"] = bad
        with pytest.raises(DefinitionError) as exc:
            parse_definition(data)
        assert any(f.startswith("index_event.concept_ids") for f in fields(exc)), bad


@pytest.mark.parametrize("window", [{"start_days": -36501, "end_days": 0}, {"start_days": 0, "end_days": 36501}])
def test_window_bounds_are_limited_to_a_century(window):
    data = reference()
    data["rules"] = [rule(window=window)]
    with pytest.raises(DefinitionError):
        parse_definition(data)


def test_date_range_start_after_end_fails_and_dates_parse():
    data = reference()
    data["index_event"]["date_range"] = {"start": "2024-12-31", "end": "2015-01-01"}
    with pytest.raises(DefinitionError) as exc:
        parse_definition(data)
    assert "index_event.date_range.end" in fields(exc)
    data["index_event"]["date_range"] = {"start": "2015-01-01", "end": "2024-12-31"}
    assert str(parse_definition(data).index_event.date_range.start) == "2015-01-01"


def test_date_range_allows_open_ends():
    data = reference()
    data["index_event"]["date_range"] = {"start": "2015-01-01"}
    assert parse_definition(data).index_event.date_range.end is None


@pytest.mark.parametrize("domain", ["measurement", "procedure", "", "Drug"])
def test_unsupported_domains_are_rejected(domain):
    data = reference()
    data["index_event"]["domain"] = domain
    with pytest.raises(DefinitionError) as exc:
        parse_definition(data)
    assert "index_event.domain" in fields(exc)
    data = reference()
    data["rules"] = [rule(domain=domain)]
    with pytest.raises(DefinitionError) as exc:
        parse_definition(data)
    assert "rules.0.domain" in fields(exc)


def test_rule_type_must_be_include_or_exclude():
    data = reference()
    data["rules"] = [rule(type="maybe")]
    with pytest.raises(DefinitionError) as exc:
        parse_definition(data)
    assert "rules.0.type" in fields(exc)


@pytest.mark.parametrize("label", ["", "   ", "x" * 101])
def test_rule_label_is_required_and_bounded(label):
    data = reference()
    data["rules"] = [rule(label=label)]
    with pytest.raises(DefinitionError) as exc:
        parse_definition(data)
    assert "rules.0.label" in fields(exc)


def test_name_is_required_and_bounded():
    for name in (None, "", "  ", "x" * 201):
        data = reference()
        data["name"] = name
        with pytest.raises(DefinitionError) as exc:
            parse_definition(data)
        assert "name" in fields(exc), name


@pytest.mark.parametrize("value", [0, -1, 101])
def test_min_occurrences_is_between_1_and_100(value):
    data = reference()
    data["rules"] = [rule(min_occurrences=value)]
    with pytest.raises(DefinitionError) as exc:
        parse_definition(data)
    assert "rules.0.min_occurrences" in fields(exc)


def test_observation_days_cannot_be_negative():
    data = reference()
    data["observation"] = {"prior_days": -1, "post_days": -5}
    with pytest.raises(DefinitionError) as exc:
        parse_definition(data)
    errors = fields(exc)
    assert "observation.prior_days" in errors and "observation.post_days" in errors


def test_unknown_fields_are_rejected_so_typos_cannot_silently_drop_a_criterion():
    data = reference()
    data["demographics"]["age_mn"] = 40
    data["rules"][0]["inclde_descendants"] = False
    with pytest.raises(DefinitionError) as exc:
        parse_definition(data)
    errors = fields(exc)
    assert "demographics.age_mn" in errors and "rules.0.inclde_descendants" in errors


def test_all_errors_are_reported_at_once():
    data = reference()
    data["demographics"] = {"age_min": 80, "age_max": 40}
    data["rules"] = [rule(concept_ids=[]), rule(window={"start_days": 5, "end_days": 1})]
    with pytest.raises(DefinitionError) as exc:
        parse_definition(data)
    assert set(fields(exc)) >= {"demographics.age_max", "rules.0.concept_ids", "rules.1.window.end_days"}


@pytest.mark.parametrize("raw", [None, [], "text", 5])
def test_non_object_input_is_a_single_clear_error(raw):
    with pytest.raises(DefinitionError) as exc:
        parse_definition(raw)
    assert len(exc.value.errors) == 1
    assert exc.value.errors[0]["field"] == ""


def test_error_messages_do_not_echo_submitted_values():
    """Telemetry/privacy rule: nothing the user typed (labels, names) is echoed back inside error messages."""
    data = reference()
    data["name"] = "SECRET-NAME-MARKER" * 20
    data["rules"] = [rule(label="PRIVATE-LABEL-MARKER" * 10, concept_ids=[])]
    with pytest.raises(DefinitionError) as exc:
        parse_definition(data)
    text = json.dumps(exc.value.errors)
    assert "SECRET-NAME-MARKER" not in text and "PRIVATE-LABEL-MARKER" not in text
