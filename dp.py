import asyncpg

from config import settings

_pool: asyncpg.Pool | None = None


async def criar_pool() -> None:
    """Abre o pool. Chamado UMA vez, no startup da aplicação."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=settings.pool_min_size,
            max_size=settings.pool_max_size,
        )


async def fechar_pool() -> None:
    """Fecha o pool. Chamado no shutdown da aplicação."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    """Devolve o pool já aberto. Erro claro se chamado antes do startup."""
    if _pool is None:
        raise RuntimeError("Pool não inicializado — chame criar_pool() no startup.")
    return _pool