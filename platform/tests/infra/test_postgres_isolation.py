"""Database isolation is enforced by Postgres, not by good intentions.

Ten agents write ten services. The guarantee that one cannot read another's
data has to be a property of the engine, provable in a test — which is what
these are. Acceptance criteria 2 and 5.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from conftest import requires_port

pytestmark = pytest.mark.infra

DB_SERVICES = (
    "crawler",
    "enricher",
    "fitment",
    "matcher",
    "catalog",
    "search",
    "billing",
    "ops",
)


@pytest.fixture(scope="module")
def pg(env: dict[str, str]) -> dict[str, Any]:
    host = "127.0.0.1"
    port = int(env.get("POSTGRES_HOST_PORT", "15432"))
    requires_port(host, port, "postgres")
    return {"host": host, "port": port, "env": env}


def connect(pg: dict[str, Any], user: str, dbname: str, password: str) -> psycopg.Connection[Any]:
    return psycopg.connect(
        host=pg["host"],
        port=pg["port"],
        user=user,
        dbname=dbname,
        password=password,
        connect_timeout=10,
    )


def service_password(env: dict[str, str], service: str) -> str:
    return env[f"{service.upper()}_DB_PASSWORD"]


def test_eight_databases_exist(pg: dict[str, Any]) -> None:
    with connect(pg, "postgres", "postgres", pg["env"]["POSTGRES_PASSWORD"]) as conn:
        rows = conn.execute(
            "SELECT datname FROM pg_database WHERE datname LIKE 'yadakchi\\_%'"
        ).fetchall()
    assert {row[0] for row in rows} == {f"yadakchi_{s}" for s in DB_SERVICES}


@pytest.mark.parametrize("service", DB_SERVICES)
def test_service_can_use_its_own_database(pg: dict[str, Any], service: str) -> None:
    """And can create tables — Django migrations need ownership of public."""
    password = service_password(pg["env"], service)
    with connect(pg, service, f"yadakchi_{service}", password) as conn:
        assert conn.execute("SELECT current_user, current_database()").fetchone() == (
            service,
            f"yadakchi_{service}",
        )
        conn.execute("CREATE TABLE IF NOT EXISTS _isolation_probe (id int primary key)")
        conn.execute("DROP TABLE _isolation_probe")


def test_enricher_cannot_connect_to_matcher(pg: dict[str, Any]) -> None:
    """The exact scenario from the spec's acceptance criteria."""
    with pytest.raises(psycopg.OperationalError) as excinfo:
        connect(pg, "enricher", "yadakchi_matcher", service_password(pg["env"], "enricher"))
    assert "permission denied" in str(excinfo.value).lower()


@pytest.mark.parametrize("service", DB_SERVICES)
def test_no_service_can_reach_any_other_database(pg: dict[str, Any], service: str) -> None:
    """Every one of the 56 forbidden pairs, not just the one in the spec."""
    password = service_password(pg["env"], service)
    for other in DB_SERVICES:
        if other == service:
            continue
        with pytest.raises(psycopg.OperationalError):
            connect(pg, service, f"yadakchi_{other}", password)


def test_service_roles_cannot_create_databases_or_roles(pg: dict[str, Any]) -> None:
    with connect(pg, "postgres", "postgres", pg["env"]["POSTGRES_PASSWORD"]) as conn:
        rows = conn.execute(
            "SELECT rolname, rolcreatedb, rolcreaterole, rolsuper FROM pg_roles "
            "WHERE rolname = ANY(%s)",
            (list(DB_SERVICES),),
        ).fetchall()
    assert len(rows) == len(DB_SERVICES)
    for name, createdb, createrole, superuser in rows:
        assert not createdb, name
        assert not createrole, name
        assert not superuser, name


def test_public_cannot_connect(pg: dict[str, Any]) -> None:
    """CONNECT was revoked from PUBLIC, so a new role gets nothing by default."""
    with connect(pg, "postgres", "postgres", pg["env"]["POSTGRES_PASSWORD"]) as conn:
        conn.execute(
            "SELECT datname, has_database_privilege('public', datname, 'CONNECT') "
            "FROM pg_database WHERE datname LIKE 'yadakchi\\_%'"
        )
        for datname, can_connect in conn.execute(
            "SELECT datname, has_database_privilege('public', datname, 'CONNECT') "
            "FROM pg_database WHERE datname LIKE 'yadakchi\\_%'"
        ).fetchall():
            assert not can_connect, datname


def test_pgvector_is_available_in_matcher(pg: dict[str, Any]) -> None:
    """Acceptance criterion 5, proved by actually using the type."""
    with connect(pg, "matcher", "yadakchi_matcher", service_password(pg["env"], "matcher")) as conn:
        installed = {row[0] for row in conn.execute("SELECT extname FROM pg_extension").fetchall()}
        assert "vector" in installed
        distance = conn.execute("SELECT '[1,0,0]'::vector <-> '[0,1,0]'::vector").fetchone()
        assert distance is not None and distance[0] == pytest.approx(1.4142135, rel=1e-5)


@pytest.mark.parametrize("service", ["matcher", "search"])
def test_pg_trgm_is_available(pg: dict[str, Any], service: str) -> None:
    with connect(pg, service, f"yadakchi_{service}", service_password(pg["env"], service)) as conn:
        installed = {row[0] for row in conn.execute("SELECT extname FROM pg_extension").fetchall()}
        assert "pg_trgm" in installed
        row = conn.execute("SELECT similarity('لنت ترمز', 'لنت ترمز جلو')").fetchone()
        assert row is not None and 0.0 < float(row[0]) < 1.0


def test_no_cross_database_extensions(pg: dict[str, Any]) -> None:
    """No FDW, no dblink: isolation must not be bypassable from inside SQL."""
    with connect(pg, "postgres", "postgres", pg["env"]["POSTGRES_PASSWORD"]) as conn:
        for service in DB_SERVICES:
            with psycopg.connect(
                host=pg["host"],
                port=pg["port"],
                user="postgres",
                dbname=f"yadakchi_{service}",
                password=pg["env"]["POSTGRES_PASSWORD"],
                connect_timeout=10,
            ) as db:
                installed = {
                    row[0] for row in db.execute("SELECT extname FROM pg_extension").fetchall()
                }
                assert not installed & {"postgres_fdw", "dblink"}, service
        assert conn.execute("SELECT 1").fetchone() == (1,)
