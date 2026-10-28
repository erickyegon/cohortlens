-- Used by `scripts/seed_cloud.sh --rotate-passwords`: new passwords for the two existing roles (psql variables, as in
-- 00-roles.sql). The script writes the matching new secret versions in the same run.
ALTER ROLE cohortlens_reader PASSWORD :'reader_pw';
ALTER ROLE cohortlens_app PASSWORD :'app_pw';
