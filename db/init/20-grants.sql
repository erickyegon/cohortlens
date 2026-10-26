-- Runs after the OMOP seed (10-omop_subset.sql.gz) has created and filled schema cdm.
-- cdm is read-only for the application; app belongs to cohortlens_app; neither role may create objects elsewhere.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
DO $$
BEGIN
  -- no implicit CONNECT / TEMP for everyone; only the two application roles may connect
  EXECUTE format('REVOKE CONNECT, TEMPORARY ON DATABASE %I FROM PUBLIC', current_database());
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO cohortlens_reader, cohortlens_app', current_database());
END $$;
GRANT USAGE ON SCHEMA cdm TO cohortlens_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA cdm TO cohortlens_reader;
REVOKE ALL ON SCHEMA app FROM cohortlens_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA cdm GRANT SELECT ON TABLES TO cohortlens_reader;
-- belt and braces: enforced by the server, not only by the client options the API sets
ALTER ROLE cohortlens_reader SET default_transaction_read_only = on;
ALTER ROLE cohortlens_reader SET statement_timeout = '10s';
