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
    assert definition.index_event.domain == "drug"
    assert definition.index_event.concept_ids == [1308216]
    assert definition.index_event.include_descendants and definition.index_event.first_occurrence_only
    assert definition.demographics.age_min == 50 and definition.demographics.age_max is None
    assert definition.demographics.sex == ["F", "M"]
    assert definition.observation.prior_days == 365 and definition.observation.post_days == 0
    washout, hypertension, heart_failure = definition.rules
    assert (washout.type, washout.domain, washout.concept_ids) == ("exclude", "drug", [21601784])
    assert (washout.window.start_days, washout.window.end_days) == (-365, -1)
    assert (hypertension.type, hypertension.domain, hypertension.concept_ids) == ("include", "condition", [320128])
    assert (hypertension.window.start_days, hypertension.window.end_days) == (ANY_TIME_BEFORE, 0)
    assert (heart_failure.type, heart_failure.concept_ids) == ("exclude", [316139])
    assert (heart_failure.window.start_days, heart_failure.window.end_days) == (ANY_TIME_BEFORE, -1)
    assert all(r.include_descendants and r.min_occurrences == 1 for r in definition.rules)


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


def test_ten_rules_are_allowed():
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
            "assert 'pydantic' in sys.modules; "
            "banned = ('sqlalchemy', 'psycopg', 'psycopg2', 'asyncpg', 'pg8000'); "
            "bad = [m for m in sys.modules if m.split('.')[0] in banned or m.startswith('app.db')]; "
            "print(bad); sys.exit(1 if bad else 0)")
    result = subprocess.run([sys.executable, "-c", code], cwd=API_DIR, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


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
    # the typo'd key itself is user text and is masked; the parent path still locates the problem
    assert "demographics.<unknown field>" in errors and "rules.0.<unknown field>" in errors


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


# ---- review round 1 on PR #34 -------------------------------------------------------------------------------

def test_unknown_keys_are_reported_without_echoing_them():
    """The key itself is user text: it must never reach the error (logs, telemetry, responses)."""
    marker = "zzSECRET\x1b[31m\n"
    data = reference()
    data[marker] = 1
    data["rules"][1]["window"][marker] = 2
    data["demographics"][marker + "b"] = 3
    with pytest.raises(DefinitionError) as exc:
        parse_definition(data)
    assert "zzSECRET" not in json.dumps(exc.value.errors) and "zzSECRET" not in str(exc.value)
    assert {"<unknown field>", "rules.1.window.<unknown field>", "demographics.<unknown field>"} <= set(fields(exc))


def test_non_string_keys_are_reported_without_echoing_them():
    with pytest.raises(DefinitionError) as exc:
        parse_definition({"name": "x", "index_event": {"domain": "drug", "concept_ids": [1]}, 12345: "v"})
    assert "12345" not in json.dumps(exc.value.errors)


def test_concept_ids_fit_a_postgres_integer():
    data = reference()
    data["index_event"]["concept_ids"] = [2_147_483_647]
    parse_definition(data)
    for too_big in (2_147_483_648, 2**80):
        data["index_event"]["concept_ids"] = [too_big]
        with pytest.raises(DefinitionError) as exc:
            parse_definition(data)
        assert any(f.startswith("index_event.concept_ids") for f in fields(exc))


def test_too_many_rules_does_not_hide_the_other_errors():
    data = {**reference(), "rules": [rule(label=f"r{i}", concept_ids=[]) for i in range(11)]}
    with pytest.raises(DefinitionError) as exc:
        parse_definition(data)
    errors = fields(exc)
    assert "rules" in errors and "rules.0.concept_ids" in errors and "rules.10.concept_ids" in errors


@pytest.mark.parametrize("flag", ["yes", "no", "on", "1", 1, 0, "true", None])
def test_boolean_flags_are_strict(flag):
    data = reference()
    data["index_event"]["include_descendants"] = flag
    data["rules"][0]["include_descendants"] = flag
    data["index_event"]["first_occurrence_only"] = flag
    with pytest.raises(DefinitionError) as exc:
        parse_definition(data)
    expected = {"index_event.include_descendants", "rules.0.include_descendants", "index_event.first_occurrence_only"}
    assert expected <= set(fields(exc))


@pytest.mark.parametrize("bad", [0, 86400, "2020-01-01T00:00:00", "01/02/2020", "2020-1-1", "0001-01-01",
                                 "9999-12-31", "1899-12-31", "2101-01-01"])
def test_date_range_accepts_only_plausible_iso_dates(bad):
    data = reference()
    data["index_event"]["date_range"] = {"start": bad}
    with pytest.raises(DefinitionError) as exc:
        parse_definition(data)
    assert "index_event.date_range.start" in fields(exc)


def test_date_range_accepts_iso_dates_at_the_limits():
    data = reference()
    data["index_event"]["date_range"] = {"start": "1900-01-01", "end": "2100-12-31"}
    parse_definition(data)


CONTROL_TEXT = ["a\x00b", "x\r\ny", "\u202e", "  \u200b ", "tab\there", "\x1b[31m", "a\u2028b", "\ufeff"]


@pytest.mark.parametrize("text", CONTROL_TEXT)
def test_names_and_labels_reject_control_and_invisible_characters(text):
    data = reference()
    data["name"] = text
    data["rules"] = [rule(label=text)]
    with pytest.raises(DefinitionError) as exc:
        parse_definition(data)
    assert {"name", "rules.0.label"} <= set(fields(exc))


def test_names_and_labels_accept_ordinary_unicode_and_are_nfc_normalised():
    data = reference()
    data["name"] = "Hipertensi\u00f3n \u2013 dosis \u226550 mg"
    data["rules"] = [rule(label="cafe\u0301")]  # 'e' + combining acute accent
    definition = parse_definition(data)
    assert definition.name == data["name"]
    assert definition.rules[0].label == "caf\u00e9"


def test_rule_labels_must_be_unique_ignoring_case():
    data = reference()
    data["rules"] = [rule(label="Heart failure"), rule(label="  heart FAILURE ")]
    with pytest.raises(DefinitionError) as exc:
        parse_definition(data)
    assert "unique" in fields(exc)["rules"]
    data["rules"] = [rule(label="Heart failure"), rule(label="Hypertension")]
    parse_definition(data)


def test_sex_and_rule_concept_ids_are_deduplicated():
    data = reference()
    data["demographics"] = {"sex": ["F", "F"]}
    data["rules"] = [rule(concept_ids=[5, 5, 4, 5])]
    definition = parse_definition(data)
    assert definition.demographics.sex == ["F"]
    assert definition.rules[0].concept_ids == [5, 4]


def test_text_and_enum_fields_reject_bytes():
    data = reference()
    data["name"] = b"bytes name"
    data["rules"] = [rule(label=b"abc", domain=b"condition")]
    data["demographics"] = {"sex": [b"F"]}
    with pytest.raises(DefinitionError) as exc:
        parse_definition(data)
    assert {"name", "rules.0.label", "rules.0.domain"} <= set(fields(exc))
    assert any(f.startswith("demographics.sex") for f in fields(exc))


@pytest.mark.parametrize("fullwidth", ["２０２０-０１-０１", "٢٠٢٠-01-01"])
def test_date_strings_must_use_ascii_digits(fullwidth):
    data = reference()
    data["index_event"]["date_range"] = {"start": fullwidth}
    with pytest.raises(DefinitionError) as exc:
        parse_definition(data)
    assert fields(exc)["index_event.date_range.start"] == "must be a date in YYYY-MM-DD format"
