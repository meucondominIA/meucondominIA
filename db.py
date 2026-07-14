import asyncio
import json
import logging

import asyncpg
from pgvector.asyncpg import register_vector

from config import settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def _registrar_codecs(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )
    await register_vector(conn, schema=settings.pgvector_schema)


async def criar_pool() -> None:
    """Abre o pool. Chamado UMA vez, no startup da aplicação."""
    global _pool #altera a variavel do modulo, não local, pro get_pool() achar
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=settings.pool_min_size,
            max_size=settings.pool_max_size,
            init=_registrar_codecs,
        )


async def fechar_pool() -> None:
    """Fecha o pool. Chamado no shutdown da aplicação."""
    global _pool
    if _pool is None:
        return
    try:
        await asyncio.wait_for(_pool.close(), timeout=10)
    except TimeoutError:
        logger.warning("pool: close() excedeu 10s — encerrando conexões à força")
        _pool.terminate()
    finally:
        _pool = None


def get_pool() -> asyncpg.Pool:
    """Devolve o pool já aberto. Erro claro se chamado antes do startup."""
    if _pool is None:
        raise RuntimeError("Pool não inicializado — chame criar_pool() no startup.")
    return _pool
