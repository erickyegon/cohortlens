"""Convert Synthea CSV output into the nine OMOP tables CohortLens uses and write the Postgres seed.

Dev-time tool only: runtime (docker compose) loads the committed seed and needs none of this.

    pip install duckdb
    python data/etl/synthea_to_omop.py \
        --synthea  <dir with patients.csv, encounters.csv, conditions.csv, medications.csv, observations.csv> \
        --vocab    <dir with concept.parquet, concept_ancestor.parquet, concept_relationship.parquet> \
        --out      data/seed/omop_subset.sql.gz

Generation used for the committed seed: Synthea master-branch-latest, seed 42, population 5000, Massachusetts.
"""
import argparse
import gzip
import os
import shutil
import tempfile

import duckdb

# LOINC codes loaded into `measurement` (blood pressure and HbA1c only).
MEASUREMENT_LOINC = ("8480-6", "8462-4", "85354-9", "4548-4")

DDL = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cdm_ddl.sql"), encoding="utf-8").read()

TABLES = {
    "person": ["person_id", "gender_concept_id", "year_of_birth", "month_of_birth", "day_of_birth", "birth_datetime",
               "race_concept_id", "ethnicity_concept_id", "person_source_value", "gender_source_value",
               "race_source_value", "ethnicity_source_value"],
    "observation_period": ["observation_period_id", "person_id", "observation_period_start_date",
                           "observation_period_end_date", "period_type_concept_id"],
    "visit_occurrence": ["visit_occurrence_id", "person_id", "visit_concept_id", "visit_start_date", "visit_end_date",
                         "visit_type_concept_id", "visit_source_value"],
    "condition_occurrence": ["condition_occurrence_id", "person_id", "condition_concept_id", "condition_start_date",
                             "condition_end_date", "condition_type_concept_id", "visit_occurrence_id",
                             "condition_source_value"],
    "drug_exposure": ["drug_exposure_id", "person_id", "drug_concept_id", "drug_exposure_start_date",
                      "drug_exposure_end_date", "drug_type_concept_id", "days_supply", "visit_occurrence_id",
                      "drug_source_value"],
    "measurement": ["measurement_id", "person_id", "measurement_concept_id", "measurement_date",
                    "measurement_type_concept_id", "value_as_number", "unit_source_value", "visit_occurrence_id",
                    "measurement_source_value"],
    "death": ["person_id", "death_date", "death_type_concept_id"],
    "concept": ["concept_id", "concept_name", "domain_id", "vocabulary_id", "concept_class_id", "standard_concept",
                "concept_code", "invalid_reason"],
    "concept_ancestor": ["ancestor_concept_id", "descendant_concept_id", "min_levels_of_separation",
                         "max_levels_of_separation"],
}

POST_LOAD = """
ALTER TABLE cdm.person ADD PRIMARY KEY (person_id);
ALTER TABLE cdm.observation_period ADD PRIMARY KEY (observation_period_id);
ALTER TABLE cdm.visit_occurrence ADD PRIMARY KEY (visit_occurrence_id);
ALTER TABLE cdm.condition_occurrence ADD PRIMARY KEY (condition_occurrence_id);
ALTER TABLE cdm.drug_exposure ADD PRIMARY KEY (drug_exposure_id);
ALTER TABLE cdm.measurement ADD PRIMARY KEY (measurement_id);
ALTER TABLE cdm.death ADD PRIMARY KEY (person_id);
ALTER TABLE cdm.concept ADD PRIMARY KEY (concept_id);
ALTER TABLE cdm.concept_ancestor ADD PRIMARY KEY (ancestor_concept_id, descendant_concept_id);
CREATE INDEX idx_obs_period_person ON cdm.observation_period (person_id, observation_period_start_date, observation_period_end_date);
CREATE INDEX idx_visit_person ON cdm.visit_occurrence (person_id, visit_start_date);
CREATE INDEX idx_condition_concept_person_date ON cdm.condition_occurrence (condition_concept_id, person_id, condition_start_date);
CREATE INDEX idx_condition_person ON cdm.condition_occurrence (person_id);
CREATE INDEX idx_drug_concept_person_date ON cdm.drug_exposure (drug_concept_id, person_id, drug_exposure_start_date);
CREATE INDEX idx_drug_person ON cdm.drug_exposure (person_id);
CREATE INDEX idx_measurement_concept_person ON cdm.measurement (measurement_concept_id, person_id, measurement_date);
CREATE INDEX idx_concept_ancestor_ancestor ON cdm.concept_ancestor (ancestor_concept_id);
CREATE INDEX idx_concept_ancestor_descendant ON cdm.concept_ancestor (descendant_concept_id);
CREATE INDEX idx_concept_name ON cdm.concept (lower(concept_name) text_pattern_ops);
CREATE INDEX idx_concept_domain ON cdm.concept (domain_id, standard_concept);
ANALYZE;
"""


def build(con, synthea, vocab):
    s = synthea.replace("\\", "/")
    v = vocab.replace("\\", "/")
    con.execute(f"create view concept_full as select * from read_parquet('{v}/concept.parquet')")
    con.execute(f"create view rel_full as select * from read_parquet('{v}/concept_relationship.parquet') where relationship_id='Maps to'")
    con.execute(f"create view anc_full as select * from read_parquet('{v}/concept_ancestor.parquet')")
    for name in ("patients", "encounters", "conditions", "medications", "observations"):
        con.execute(f"create view raw_{name} as select * from read_csv_auto('{s}/{name}.csv', all_varchar=true)")

    # code -> standard concept (direct if standard, else via 'Maps to'); RxNorm preferred over RxNorm Extension.
    # Only codes present in the Synthea data are looked up, so the large vocabulary scans stay cheap.
    def code_map(name, vocabs, domain, src_sql):
        vl = ",".join(f"'{x}'" for x in vocabs)
        con.execute(f"create table src_{name} as {src_sql}")
        con.execute(f"""create table cand_{name} as
          select concept_id, concept_code, vocabulary_id, standard_concept
          from read_parquet('{v}/concept.parquet')
          where vocabulary_id in ({vl}) and concept_code in (select code from src_{name})""")
        con.execute(f"""create table rel_{name} as
          select concept_id_1, concept_id_2 from read_parquet('{v}/concept_relationship.parquet')
          where relationship_id='Maps to' and concept_id_1 in (select concept_id from cand_{name})""")
        con.execute(f"""create table tgt_{name} as
          select concept_id, domain_id from read_parquet('{v}/concept.parquet')
          where standard_concept='S' and concept_id in (
            select concept_id from cand_{name} union select concept_id_2 from rel_{name})""")
        con.execute(f"""create table {name} as
          select concept_code, concept_id from (
            select c.concept_code, t.concept_id,
                   row_number() over (partition by c.concept_code order by case c.vocabulary_id when 'RxNorm' then 0 else 1 end, c.concept_id, t.concept_id) rn
            from cand_{name} c
            left join rel_{name} r on r.concept_id_1=c.concept_id
            join tgt_{name} t on t.concept_id = case when c.standard_concept='S' then c.concept_id else r.concept_id_2 end
            where t.domain_id='{domain}') x
          where rn=1""")

    code_map("map_cond", ["SNOMED"], "Condition", "select distinct CODE code from raw_conditions")
    code_map("map_drug", ["RxNorm", "RxNorm Extension"], "Drug", "select distinct CODE code from raw_medications")
    code_map("map_meas", ["LOINC"], "Measurement", "select unnest(" + repr(list(MEASUREMENT_LOINC)) + ") code")

    con.execute("""create table person as
      select dense_rank() over (order by Id) person_id, Id src,
             case GENDER when 'M' then 8507 when 'F' then 8532 else 0 end gender_concept_id,
             year(BIRTHDATE::date) year_of_birth, month(BIRTHDATE::date) month_of_birth, day(BIRTHDATE::date) day_of_birth,
             BIRTHDATE::timestamp birth_datetime,
             case RACE when 'white' then 8527 when 'black' then 8516 when 'asian' then 8515 when 'native' then 8657 else 0 end race_concept_id,
             case ETHNICITY when 'hispanic' then 38003563 else 38003564 end ethnicity_concept_id,
             Id person_source_value, GENDER gender_source_value, RACE race_source_value, ETHNICITY ethnicity_source_value,
             DEATHDATE::date death_date
      from raw_patients""")
    con.execute("""create table visit_occurrence as
      select row_number() over (order by e.START, e.Id) visit_occurrence_id, p.person_id, e.Id src,
             case lower(e.ENCOUNTERCLASS) when 'inpatient' then 9201 when 'emergency' then 9203 when 'urgentcare' then 9203
                  else 9202 end visit_concept_id,
             e.START::timestamp::date visit_start_date, coalesce(e.STOP, e.START)::timestamp::date visit_end_date,
             32817 visit_type_concept_id, e.ENCOUNTERCLASS visit_source_value
      from raw_encounters e join person p on p.src=e.PATIENT""")
    con.execute("""create table condition_occurrence as
      select row_number() over (order by c.START, c.PATIENT, c.CODE) condition_occurrence_id, p.person_id, m.concept_id condition_concept_id,
             c.START::date condition_start_date, c.STOP::date condition_end_date, 32020 condition_type_concept_id,
             v.visit_occurrence_id, c.CODE condition_source_value
      from raw_conditions c join person p on p.src=c.PATIENT join map_cond m on m.concept_code=c.CODE
      left join visit_occurrence v on v.src=c.ENCOUNTER""")
    con.execute("""create table drug_exposure as
      select row_number() over (order by d.START, d.PATIENT, d.CODE) drug_exposure_id, p.person_id, m.concept_id drug_concept_id,
             d.START::timestamp::date drug_exposure_start_date,
             coalesce(d.STOP, d.START)::timestamp::date drug_exposure_end_date, 32818 drug_type_concept_id,
             greatest(1, date_diff('day', d.START::timestamp::date, coalesce(d.STOP, d.START)::timestamp::date)) days_supply,
             v.visit_occurrence_id, d.CODE drug_source_value
      from raw_medications d join person p on p.src=d.PATIENT join map_drug m on m.concept_code=d.CODE
      left join visit_occurrence v on v.src=d.ENCOUNTER""")
    con.execute("""create table measurement as
      select row_number() over (order by o.DATE, o.PATIENT, o.CODE) measurement_id, p.person_id, m.concept_id measurement_concept_id,
             o.DATE::timestamp::date measurement_date, 32817 measurement_type_concept_id,
             try_cast(o.VALUE as double) value_as_number, o.UNITS unit_source_value, v.visit_occurrence_id,
             o.CODE measurement_source_value
      from raw_observations o join person p on p.src=o.PATIENT join map_meas m on m.concept_code=o.CODE
      left join visit_occurrence v on v.src=o.ENCOUNTER
      where try_cast(o.VALUE as double) is not null""")
    con.execute("""create table death as
      select person_id, death_date, 32817 death_type_concept_id from person where death_date is not null""")
    # observation period spans every dated record of the person; it ends at death (or the last record if later)
    con.execute("""create table observation_period as
      with ev as (
        select person_id, visit_start_date d from visit_occurrence union all select person_id, visit_end_date from visit_occurrence
        union all select person_id, condition_start_date from condition_occurrence
        union all select person_id, drug_exposure_start_date from drug_exposure union all select person_id, drug_exposure_end_date from drug_exposure
        union all select person_id, measurement_date from measurement)
      select row_number() over (order by p.person_id) observation_period_id, p.person_id,
             min(ev.d) observation_period_start_date,
             greatest(max(ev.d), coalesce(p.death_date, max(ev.d))) observation_period_end_date,
             32817 period_type_concept_id
      from person p join ev using(person_id) group by p.person_id, p.death_date""")

    # trimmed vocabulary: every concept used in the data plus all its ancestors
    con.execute("""create table data_ids as select distinct c from (
        select condition_concept_id c from condition_occurrence union all select drug_concept_id from drug_exposure
        union all select measurement_concept_id from measurement union all select visit_concept_id from visit_occurrence
        union all select gender_concept_id from person union all select race_concept_id from person
        union all select ethnicity_concept_id from person)""")
    con.execute("create table concept_ancestor as select * from anc_full where descendant_concept_id in (select c from data_ids)")
    con.execute("create table keep_ids as select c from data_ids union select ancestor_concept_id from concept_ancestor")
    con.execute("create table concept as select * from concept_full where concept_id in (select c from keep_ids)")


def write_seed(con, out):
    tmp = tempfile.mkdtemp()
    try:
        with gzip.open(out, "wt", encoding="utf-8", newline="\n") as gz:
            gz.write("-- CohortLens OMOP subset seed (synthetic Synthea data; see data/etl/synthea_to_omop.py)\n")
            gz.write("SET client_encoding = 'UTF8';\n")
            gz.write(DDL + "\n")
            for t, cols in TABLES.items():
                f = os.path.join(tmp, t + ".csv").replace("\\", "/")
                con.execute(f"copy (select {', '.join(cols)} from {t}) to '{f}' (format csv, header false, nullstr '')")
                gz.write(f"COPY cdm.{t} ({', '.join(cols)}) FROM stdin WITH (FORMAT csv, NULL '');\n")
                with open(f, encoding="utf-8", newline="") as fh:
                    shutil.copyfileobj(fh, gz)
                gz.write("\\.\n\n")
            gz.write(POST_LOAD)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthea", required=True)
    ap.add_argument("--vocab", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--db", default=":memory:")
    a = ap.parse_args()
    con = duckdb.connect(a.db)
    build(con, a.synthea, a.vocab)
    for t in TABLES:
        print(f"{t}: {con.execute(f'select count(*) from {t}').fetchone()[0]:,} rows")
    write_seed(con, a.out)
    print("wrote", a.out, f"{os.path.getsize(a.out) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
