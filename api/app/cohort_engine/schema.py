"""SQLAlchemy Core descriptions of the OMOP tables the cohort engine reads (schema `cdm`, read-only).

Only the columns the compiler uses are declared; the full DDL lives in data/etl/cdm_ddl.sql.
"""
from sqlalchemy import Column, Date, Integer, MetaData, String, Table

metadata = MetaData(schema="cdm")

person = Table(
    "person", metadata,
    Column("person_id", Integer, primary_key=True),
    Column("gender_concept_id", Integer),
    Column("year_of_birth", Integer),
    Column("month_of_birth", Integer),
    Column("day_of_birth", Integer),
)

observation_period = Table(
    "observation_period", metadata,
    Column("observation_period_id", Integer, primary_key=True),
    Column("person_id", Integer),
    Column("observation_period_start_date", Date),
    Column("observation_period_end_date", Date),
)

condition_occurrence = Table(
    "condition_occurrence", metadata,
    Column("condition_occurrence_id", Integer, primary_key=True),
    Column("person_id", Integer),
    Column("condition_concept_id", Integer),
    Column("condition_start_date", Date),
)

drug_exposure = Table(
    "drug_exposure", metadata,
    Column("drug_exposure_id", Integer, primary_key=True),
    Column("person_id", Integer),
    Column("drug_concept_id", Integer),
    Column("drug_exposure_start_date", Date),
)

concept_ancestor = Table(
    "concept_ancestor", metadata,
    Column("ancestor_concept_id", Integer, primary_key=True),
    Column("descendant_concept_id", Integer, primary_key=True),
)

concept = Table(
    "concept", metadata,
    Column("concept_id", Integer, primary_key=True),
    Column("concept_name", String),
    Column("domain_id", String),
    Column("standard_concept", String),
    Column("invalid_reason", String),
)

# domain -> (table, concept column, event date column)
DOMAINS = {
    "condition": (condition_occurrence, condition_occurrence.c.condition_concept_id,
                  condition_occurrence.c.condition_start_date),
    "drug": (drug_exposure, drug_exposure.c.drug_concept_id, drug_exposure.c.drug_exposure_start_date),
}

# OMOP standard gender concepts
GENDER_CONCEPT = {"F": 8532, "M": 8507}
