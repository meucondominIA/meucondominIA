"""Repositório de áreas comuns (asyncpg; recebe conn, como condominios.py).

A leitura que o wizard de reserva consome: só as áreas RESERVÁVEIS do condomínio,
na ordem em que serão numeradas na lista. Espelha listar_elegiveis — filtro do
tenant e COLLATE "pt-BR-x-icu" para o container (libc) e o Supabase (ICU)
ordenarem igual; `nome` não é único, então desempata por id (queries-order.html).

Devolve só id e nome: é o que a lista precisa mostrar e o que a escrita da Etapa 2
vai referenciar. `requer_aprovacao` entra quando a escrita decidir pendente vs
aprovada — hoje não tem leitor, então fica de fora.
"""

from uuid import UUID

import asyncpg
from pydantic import BaseModel, ConfigDict

_RESERVAVEIS = """
    select id, nome
      from areas_comuns
     where condominio_id = $1 and reservavel
     order by nome collate "pt-BR-x-icu", id
"""


class AreaReservavel(BaseModel):
    """Uma área oferecível ao morador — só o que a lista precisa mostrar."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    nome: str


async def listar_areas_reservaveis(
    conn: asyncpg.Connection, condominio_id: UUID
) -> list[AreaReservavel]:
    """As áreas reserváveis do condomínio, na ordem em que serão numeradas."""
    rows = await conn.fetch(_RESERVAVEIS, condominio_id)
    return [AreaReservavel.model_validate(dict(r)) for r in rows]
