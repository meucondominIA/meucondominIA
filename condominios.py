"""Repositório de condomínios (asyncpg; recebe conn, como regras.py).

O slug é a identidade operacional do condomínio fora do banco (CLI de
ingestão): legível em linha de comando, com formato garantido por CHECK e
unicidade por UNIQUE — por isso fetchval basta (no máximo um id por slug).
Slug inexistente devolve None: quem decide o erro e a mensagem é o chamador.
Sem filtro de `ativo`: lookup puro — "não encontrado" e "desativado" são
fatos diferentes e misturá-los criaria falha enganosa na operação.

A lista do morador é o oposto: `ativo` é o que tira o tenant sintético do menu
(D2) — até aqui a coluna não tinha leitor nenhum no código.

ORDER BY explícito e com desempate porque `nome` não é único; sem ele a saída
"must not be relied on" (queries-order.html). O COLLATE não é enfeite: Supabase
ordena por ICU e o container por libc, e os dois discordam nas primeiras
posições (medido 21/07/2026). "pt-BR-x-icu" iguala os dois (collation.html).

Que o item N seja o condomínio certo NÃO se garante aqui: é o atendimento que
indexa esta lista em vez de refazer a consulta.
"""

from uuid import UUID

import asyncpg
from pydantic import BaseModel, ConfigDict

_ELEGIVEIS = """
    select id, nome
      from condominios
     where ativo
     order by nome collate "pt-BR-x-icu", id
"""


class CondominioElegivel(BaseModel):
    """Um condomínio oferecível ao morador — só o que a lista precisa mostrar."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    nome: str


async def buscar_id_por_slug(conn: asyncpg.Connection, slug: str) -> UUID | None:
    """Devolve o id do condomínio com este slug, ou None se não existir."""
    slug = slug.strip()
    if not slug:
        raise ValueError("slug em branco: não identifica condomínio")
    return await conn.fetchval("select id from condominios where slug = $1", slug)


async def listar_elegiveis(conn: asyncpg.Connection) -> list[CondominioElegivel]:
    """Os condomínios oferecíveis, na ordem em que serão numerados."""
    rows = await conn.fetch(_ELEGIVEIS)
    return [CondominioElegivel.model_validate(dict(row)) for row in rows]


async def nome_por_id(conn: asyncpg.Connection, condominio_id: UUID) -> str | None:
    """O nome do condomínio, para as mensagens que o citam (confirmação).

    Sem filtro de `ativo`: o id chega de uma FK que o banco garante existir —
    apagar o condomínio nula o pendente (on delete set null) ou leva a conversa
    junto (cascade), nunca deixa id pendurado. Desativado ainda tem nome.
    """
    return await conn.fetchval(
        "select nome from condominios where id = $1", condominio_id
    )
