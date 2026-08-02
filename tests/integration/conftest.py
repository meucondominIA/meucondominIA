"""Fixtures dos testes de integração: um Postgres efêmero em Docker.

Usa só a API core do testcontainers (DockerContainer) para subir/derrubar o
container, sem depender de driver/ORM — todo o trabalho de banco é via asyncpg,
igual à produção. A prontidão do Postgres é resolvida por um retry de conexão
(o TCP só aceita após o boot final), então não dependemos de espera por log.

Se testcontainers não estiver instalado ou o Docker indisponível, os testes de
integração são PULADOS (o resto da suíte segue normal).

Desde a Fase 5 · Etapa 2 o schema NÃO é mais um espelho mantido à mão: o banco é
construído pelas supabase/migrations/ reais, precedidas de um preâmbulo que
sintetiza o mundo do Supabase (schema auth, auth.uid(), os papéis anon/
authenticated e os grants de fábrica). O antigo schema.sql tinha 11 das 12
tabelas e mentia a nulabilidade de duas colunas, sem que nada detectasse.
"""

import asyncio
import pathlib

import asyncpg
import pytest

import db

try:
    from testcontainers.core.container import DockerContainer

    _TEM_TESTCONTAINERS = True
except ImportError:
    _TEM_TESTCONTAINERS = False

_IMAGE = "pgvector/pgvector:pg17"
_DIR = pathlib.Path(__file__).parent
_PREAMBULO = (_DIR / "preambulo_supabase.sql").read_text(encoding="utf-8")
_MIGRATIONS = sorted((_DIR.parents[1] / "supabase" / "migrations").glob("*.sql"))

# 20260706191900 faz `create extension pg_cron`, que não existe nesta imagem e
# não tem por que existir: o agendamento é da produção, não do teste. O stub dá à
# migration o que ela pede (o schema `cron` e uma `schedule` que devolve um id)
# sem fingir que há um agendador. Escrito ANTES das migrations, via `pg_config`
# para não fixar a versão do Postgres no caminho.
_STUB_PG_CRON = """set -e
DIR="$(pg_config --sharedir)/extension"
cat > "$DIR/pg_cron.control" <<'CTRL'
default_version = '1.6'
comment = 'stub dos testes de integração — NÃO é o pg_cron real'
relocatable = false
schema = pg_catalog
CTRL
cat > "$DIR/pg_cron--1.6.sql" <<'SQL'
create schema cron;
create function cron.schedule(text, text, text) returns bigint
  language sql as $stub$ select 1::bigint $stub$;
SQL
"""


async def _construir_banco(dsn: str) -> None:
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
        if not _MIGRATIONS:
            raise RuntimeError(
                "nenhuma migration encontrada em supabase/migrations/ — o banco de "
                "teste ficaria vazio e os erros apareceriam longe daqui"
            )
        await conn.execute(_PREAMBULO)
        for migration in _MIGRATIONS:
            try:
                await conn.execute(migration.read_text(encoding="utf-8"))
            except asyncpg.PostgresError as exc:
                raise RuntimeError(f"migration {migration.name}: {exc}") from exc
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
        codigo, saida = container.exec(["sh", "-c", _STUB_PG_CRON])
        if codigo != 0:
            raise RuntimeError(f"stub do pg_cron falhou: {saida!r}")
        asyncio.run(_construir_banco(dsn))
        yield dsn
    finally:
        container.stop()


@pytest.fixture
def rodar_concorrente(pg_dsn):
    """Executa `body(abrir)`, onde `abrir()` devolve conexões INDEPENDENTES.

    Corrida não cabe no rodar_tx: uma conexão só serializa tudo e o rollback do
    fim apagaria o desfecho. Aqui cada transação commita de verdade, então a
    limpeza é TRUNCATE depois de fechar as conexões (os outros testes revertem,
    logo não há dado alheio a preservar)."""

    def _rodar(body):
        async def _corpo():
            abertas = []

            async def abrir():
                conn = await asyncpg.connect(pg_dsn)
                await db._registrar_codecs(conn)
                abertas.append(conn)
                return conn

            try:
                await body(abrir)
            finally:
                for conn in abertas:
                    await conn.close()
                faxina = await asyncpg.connect(pg_dsn)
                try:
                    await faxina.execute(
                        "truncate avisos_sindico, solicitacoes, reservas, "
                        "mensagens, conversas, areas_comuns, condominios cascade"
                    )
                finally:
                    await faxina.close()

        asyncio.run(_corpo())

    return _rodar


@pytest.fixture
def rodar_tx(pg_dsn):
    """Executa `body(conn)` com os codecs do db.py, numa transação revertida no
    fim — cada teste enxerga o banco limpo (isolamento)."""

    def _rodar(body):
        async def _corpo():
            conn = await asyncpg.connect(pg_dsn)
            await db._registrar_codecs(conn)
            try:
                tr = conn.transaction()
                await tr.start()
                try:
                    await body(conn)
                finally:
                    await tr.rollback()
            finally:
                await conn.close()

        asyncio.run(_corpo())

    return _rodar
