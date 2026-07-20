"""Repositório de condomínios (asyncpg; recebe conn, como regras.py).

O slug é a identidade operacional do condomínio fora do banco (CLI de
ingestão): legível em linha de comando, com formato garantido por CHECK e
unicidade por UNIQUE — por isso fetchval basta (no máximo um id por slug).
Slug inexistente devolve None: quem decide o erro e a mensagem é o chamador.
Sem filtro de `ativo`: lookup puro — "não encontrado" e "desativado" são
fatos diferentes e misturá-los criaria falha enganosa na operação.
"""

from uuid import UUID

import asyncpg


async def buscar_id_por_slug(conn: asyncpg.Connection, slug: str) -> UUID | None:
    """Devolve o id do condomínio com este slug, ou None se não existir."""
    slug = slug.strip()
    if not slug:
        raise ValueError("slug em branco: não identifica condomínio")
    return await conn.fetchval("select id from condominios where slug = $1", slug)
