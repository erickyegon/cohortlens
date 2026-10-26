CREATE SCHEMA IF NOT EXISTS cdm;
CREATE TABLE cdm.person (
  person_id integer NOT NULL, gender_concept_id integer NOT NULL, year_of_birth integer NOT NULL,
  month_of_birth integer, day_of_birth integer, birth_datetime timestamp,
  race_concept_id integer NOT NULL, ethnicity_concept_id integer NOT NULL,
  person_source_value varchar(50), gender_source_value varchar(50),
  race_source_value varchar(50), ethnicity_source_value varchar(50));
CREATE TABLE cdm.observation_period (
  observation_period_id integer NOT NULL, person_id integer NOT NULL,
  observation_period_start_date date NOT NULL, observation_period_end_date date NOT NULL,
  period_type_concept_id integer NOT NULL);
CREATE TABLE cdm.visit_occurrence (
  visit_occurrence_id integer NOT NULL, person_id integer NOT NULL, visit_concept_id integer NOT NULL,
  visit_start_date date NOT NULL, visit_end_date date NOT NULL, visit_type_concept_id integer NOT NULL,
  visit_source_value varchar(50));
CREATE TABLE cdm.condition_occurrence (
  condition_occurrence_id integer NOT NULL, person_id integer NOT NULL, condition_concept_id integer NOT NULL,
  condition_start_date date NOT NULL, condition_end_date date, condition_type_concept_id integer NOT NULL,
  visit_occurrence_id integer, condition_source_value varchar(50));
CREATE TABLE cdm.drug_exposure (
  drug_exposure_id integer NOT NULL, person_id integer NOT NULL, drug_concept_id integer NOT NULL,
  drug_exposure_start_date date NOT NULL, drug_exposure_end_date date NOT NULL, drug_type_concept_id integer NOT NULL,
  days_supply integer, visit_occurrence_id integer, drug_source_value varchar(50));
CREATE TABLE cdm.measurement (
  measurement_id integer NOT NULL, person_id integer NOT NULL, measurement_concept_id integer NOT NULL,
  measurement_date date NOT NULL, measurement_type_concept_id integer NOT NULL, value_as_number numeric,
  unit_source_value varchar(50), visit_occurrence_id integer, measurement_source_value varchar(50));
CREATE TABLE cdm.death (
  person_id integer NOT NULL, death_date date NOT NULL, death_type_concept_id integer NOT NULL);
CREATE TABLE cdm.concept (
  concept_id integer NOT NULL, concept_name varchar(255) NOT NULL, domain_id varchar(20) NOT NULL,
  vocabulary_id varchar(20) NOT NULL, concept_class_id varchar(20) NOT NULL, standard_concept varchar(1),
  concept_code varchar(50) NOT NULL, invalid_reason varchar(1));
CREATE TABLE cdm.concept_ancestor (
  ancestor_concept_id integer NOT NULL, descendant_concept_id integer NOT NULL,
  min_levels_of_separation integer NOT NULL, max_levels_of_separation integer NOT NULL);
