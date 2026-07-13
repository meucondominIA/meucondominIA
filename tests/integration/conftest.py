"""Fixtures dos testes de integração: um Postgres efêmero em Docker.

Usa só a API core do testcontainers (DockerContainer) para subir/derrubar o
container, sem depender de driver/ORM — todo o trabalho de banco é via asyncpg,
igual à produção. A prontidão do Postgres é resolvida por um retry de conexão
(o TCP só aceita após o boot final), então não dependemos de espera por log.

Se testcontainers não estiver instalado ou o Docker indisponível, os testes de
integração são PULADOS (o resto da suíte segue normal).
"""

import asyncio
import pathlib

import asyncpg
import pytest

try:
    from testcontainers.core.container import DockerContainer

    _TEM_TESTCONTAINERS = True
except ImportError:
    _TEM_TESTCONTAINERS = False

_IMAGE = "pgvector/pgvector:pg17"
_SCHEMA = (pathlib.Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")


async def _aplicar_schema(dsn: str) -> None:
    # O TCP só aceita conexões após o boot final do Postgres: tenta com paciência.
    ultimo_erro: Exception | None = None
    conn: asyncpg.Connection | None = None
    for _ in range(40):
        try:
            conn = await asyncpg.connect(dsn)
            break
        except (OSError, asyncpg.PostgresError) as exc:
            ultimo_erro = exc
            await asyncio.sleep(0.5)
    if conn is None:
        raise RuntimeError(
            f"Postgres do container não respondeu a tempo: {ultimo_erro}"
        )
    try:
        await conn.execute(_SCHEMA)
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def pg_dsn():
    if not _TEM_TESTCONTAINERS:
        pytest.skip(
            "testcontainers não instalado (pip install -r requirements-dev.txt)"
        )

    container = (
        DockerContainer(_IMAGE)
        .with_env("POSTGRES_USER", "test")
        .with_env("POSTGRES_PASSWORD", "test")
        .with_env("POSTGRES_DB", "test")
        .with_exposed_ports(5432)
    )
    try:
        container.start()
    except Exception as exc:  # Docker fora do ar, imagem indisponível, etc.
        pytest.skip(f"Docker indisponível para os testes de integração: {exc}")

    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(5432)
        dsn = f"postgresql://test:test@{host}:{port}/test"
        asyncio.run(_aplicar_schema(dsn))
        yield dsn
    finally:
        container.stop()
