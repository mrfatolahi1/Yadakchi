-- ===========================================================================
-- yadakchi — database isolation
--
-- Runs once, on first boot of an empty PGDATA (docker-entrypoint-initdb.d).
-- Creates eight databases and eight users. Each user can connect to exactly
-- one database. The boundary between services is enforced by Postgres, not by
-- convention: an agent cannot read another service's data even by mistake.
--
-- No FDW. No dblink. No shared schema. No cross-database grants.
--
-- Databases are created with the C.UTF-8 locale, not plain C. This is not
-- cosmetic: under the C locale, pg_trgm extracts NO trigrams from Persian text
-- (show_trgm('لنت') returns '{}') and similarity() is always 0, which would
-- silently break fuzzy matching in matcher and search — the two services that
-- depend on it most. C.UTF-8 keeps byte-order collation, which is what we want
-- for deterministic indexes, while treating multibyte characters correctly.
--
-- Passwords come from the environment (psql \getenv, PG16+). If a variable is
-- unset, initialisation fails loudly — that is intentional. No secrets here.
-- ===========================================================================

\set ON_ERROR_STOP on

\getenv crawler_pw  CRAWLER_DB_PASSWORD
\getenv enricher_pw ENRICHER_DB_PASSWORD
\getenv fitment_pw  FITMENT_DB_PASSWORD
\getenv matcher_pw  MATCHER_DB_PASSWORD
\getenv catalog_pw  CATALOG_DB_PASSWORD
\getenv search_pw   SEARCH_DB_PASSWORD
\getenv billing_pw  BILLING_DB_PASSWORD
\getenv ops_pw      OPS_DB_PASSWORD

-- --------------------------------------------------------------------- crawler
CREATE ROLE crawler WITH LOGIN PASSWORD :'crawler_pw';
CREATE DATABASE yadakchi_crawler WITH OWNER = crawler ENCODING = 'UTF8' LC_COLLATE = 'C.UTF-8' LC_CTYPE = 'C.UTF-8' TEMPLATE = template0;
REVOKE ALL ON DATABASE yadakchi_crawler FROM PUBLIC;
REVOKE CONNECT ON DATABASE yadakchi_crawler FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE yadakchi_crawler TO crawler;
\connect yadakchi_crawler
ALTER SCHEMA public OWNER TO crawler;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO crawler;
\connect postgres

-- --------------------------------------------------------------------- enricher
CREATE ROLE enricher WITH LOGIN PASSWORD :'enricher_pw';
CREATE DATABASE yadakchi_enricher WITH OWNER = enricher ENCODING = 'UTF8' LC_COLLATE = 'C.UTF-8' LC_CTYPE = 'C.UTF-8' TEMPLATE = template0;
REVOKE ALL ON DATABASE yadakchi_enricher FROM PUBLIC;
REVOKE CONNECT ON DATABASE yadakchi_enricher FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE yadakchi_enricher TO enricher;
\connect yadakchi_enricher
ALTER SCHEMA public OWNER TO enricher;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO enricher;
\connect postgres

-- --------------------------------------------------------------------- fitment
CREATE ROLE fitment WITH LOGIN PASSWORD :'fitment_pw';
CREATE DATABASE yadakchi_fitment WITH OWNER = fitment ENCODING = 'UTF8' LC_COLLATE = 'C.UTF-8' LC_CTYPE = 'C.UTF-8' TEMPLATE = template0;
REVOKE ALL ON DATABASE yadakchi_fitment FROM PUBLIC;
REVOKE CONNECT ON DATABASE yadakchi_fitment FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE yadakchi_fitment TO fitment;
\connect yadakchi_fitment
ALTER SCHEMA public OWNER TO fitment;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO fitment;
\connect postgres

-- --------------------------------------------------------------------- matcher
CREATE ROLE matcher WITH LOGIN PASSWORD :'matcher_pw';
CREATE DATABASE yadakchi_matcher WITH OWNER = matcher ENCODING = 'UTF8' LC_COLLATE = 'C.UTF-8' LC_CTYPE = 'C.UTF-8' TEMPLATE = template0;
REVOKE ALL ON DATABASE yadakchi_matcher FROM PUBLIC;
REVOKE CONNECT ON DATABASE yadakchi_matcher FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE yadakchi_matcher TO matcher;
\connect yadakchi_matcher
ALTER SCHEMA public OWNER TO matcher;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO matcher;
\connect postgres

-- --------------------------------------------------------------------- catalog
CREATE ROLE catalog WITH LOGIN PASSWORD :'catalog_pw';
CREATE DATABASE yadakchi_catalog WITH OWNER = catalog ENCODING = 'UTF8' LC_COLLATE = 'C.UTF-8' LC_CTYPE = 'C.UTF-8' TEMPLATE = template0;
REVOKE ALL ON DATABASE yadakchi_catalog FROM PUBLIC;
REVOKE CONNECT ON DATABASE yadakchi_catalog FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE yadakchi_catalog TO catalog;
\connect yadakchi_catalog
ALTER SCHEMA public OWNER TO catalog;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO catalog;
\connect postgres

-- --------------------------------------------------------------------- search
CREATE ROLE search WITH LOGIN PASSWORD :'search_pw';
CREATE DATABASE yadakchi_search WITH OWNER = search ENCODING = 'UTF8' LC_COLLATE = 'C.UTF-8' LC_CTYPE = 'C.UTF-8' TEMPLATE = template0;
REVOKE ALL ON DATABASE yadakchi_search FROM PUBLIC;
REVOKE CONNECT ON DATABASE yadakchi_search FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE yadakchi_search TO search;
\connect yadakchi_search
ALTER SCHEMA public OWNER TO search;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO search;
\connect postgres

-- --------------------------------------------------------------------- billing
CREATE ROLE billing WITH LOGIN PASSWORD :'billing_pw';
CREATE DATABASE yadakchi_billing WITH OWNER = billing ENCODING = 'UTF8' LC_COLLATE = 'C.UTF-8' LC_CTYPE = 'C.UTF-8' TEMPLATE = template0;
REVOKE ALL ON DATABASE yadakchi_billing FROM PUBLIC;
REVOKE CONNECT ON DATABASE yadakchi_billing FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE yadakchi_billing TO billing;
\connect yadakchi_billing
ALTER SCHEMA public OWNER TO billing;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO billing;
\connect postgres

-- --------------------------------------------------------------------- ops
CREATE ROLE ops WITH LOGIN PASSWORD :'ops_pw';
CREATE DATABASE yadakchi_ops WITH OWNER = ops ENCODING = 'UTF8' LC_COLLATE = 'C.UTF-8' LC_CTYPE = 'C.UTF-8' TEMPLATE = template0;
REVOKE ALL ON DATABASE yadakchi_ops FROM PUBLIC;
REVOKE CONNECT ON DATABASE yadakchi_ops FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE yadakchi_ops TO ops;
\connect yadakchi_ops
ALTER SCHEMA public OWNER TO ops;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO ops;
\connect postgres

-- ===========================================================================
-- Extensions. Installed by the superuser; usable by the owning service.
--   vector   -> matcher only (embedding similarity)
--   pg_trgm  -> matcher and search (fuzzy Persian title matching)
-- ===========================================================================

\connect yadakchi_matcher
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

\connect yadakchi_search
CREATE EXTENSION IF NOT EXISTS pg_trgm;

\connect postgres

-- ===========================================================================
-- Belt and braces: nobody gets a database they do not own, and no service
-- role may create databases or roles.
-- ===========================================================================

REVOKE ALL ON DATABASE postgres FROM PUBLIC;
REVOKE ALL ON DATABASE template1 FROM PUBLIC;

DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['crawler','enricher','fitment','matcher','catalog','search','billing','ops']
  LOOP
    EXECUTE format('ALTER ROLE %I NOCREATEDB NOCREATEROLE NOSUPERUSER NOBYPASSRLS', r);
  END LOOP;
END $$;

-- Tables are created by each service's own migrations. This file creates none.
