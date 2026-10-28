-- Cloud (Neon) counterpart of db/init/00-roles.sh, fed by scripts/seed_cloud.sh. The passwords arrive as psql variables
-- (\set app_pw '...', \set reader_pw '...') and are quoted by psql (:'var'), never interpolated into the SQL text.
-- Unlike the local init script this does NOT create the schema with an owner clause naming the app role: the Neon owner
-- role is not a superuser and cannot create a schema owned by another role, so the schema is created by the owner and the
-- app role is given USAGE and CREATE on it (it then owns every table its migrations create).
CREATE ROLE cohortlens_reader LOGIN PASSWORD :'reader_pw';
CREATE ROLE cohortlens_app LOGIN PASSWORD :'app_pw';
CREATE SCHEMA IF NOT EXISTS app;
GRANT USAGE, CREATE ON SCHEMA app TO cohortlens_app;
