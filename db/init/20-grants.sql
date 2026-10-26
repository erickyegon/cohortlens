-- Runs after the OMOP seed (10-omop_subset.sql.gz) has created and filled schema cdm.
-- cdm is read-only for everyone the application uses; app belongs to cohortlens_app.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA cdm TO cohortlens_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA cdm TO cohortlens_reader;
REVOKE ALL ON SCHEMA app FROM cohortlens_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA cdm GRANT SELECT ON TABLES TO cohortlens_reader;
