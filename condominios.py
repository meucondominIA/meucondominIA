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

FRASE_QR = "Olá! Sou morador do condomínio "


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


async def resolver_por_frase(
    conn: asyncpg.Connection, texto: str | None
) -> CondominioElegivel | None:
    """O condomínio afirmado pela frase do QR (Etapa 7), ou None.

    Match exato, não busca: a frase INTEIRA é o identificador, e o `||`
    reconstrói a candidata a partir do `nome` — QR e banco derivam do mesmo
    dado, então FRASE_QR não pode ter segunda cópia em lugar nenhum.

    Filtra `ativo` ao contrário de buscar_id_por_slug: aqui quem pergunta é
    morador, e o tenant sintético do eval não pode virar atendimento.

    fetch e não fetchval porque `nome` não é único: homônimos devolvem duas
    linhas e viram None. Prédio ambíguo cai no menu, nunca no prédio errado.
    """
    if not texto:
        return None
    frase = texto.strip()
    if not frase.startswith(FRASE_QR):
        return None

    rows = await conn.fetch(
        "select id, nome from condominios where ativo and $1 = $2 || nome",
        frase,
        FRASE_QR,
    )
    if len(rows) != 1:
        return None
    return CondominioElegivel.model_validate(dict(rows[0]))


async def buscar_elegivel_por_slug(
    conn: asyncpg.Connection, slug: str
) -> CondominioElegivel | None:
    """O condomínio oferecível deste slug — a entrada da ferramenta que gera o QR.

    Filtra `ativo` porque um QR impresso de condomínio desativado é o pior
    artefato desta etapa: ninguém consegue usá-lo e ninguém descobre por quê.
    """
    slug = slug.strip()
    if not slug:
        raise ValueError("slug em branco: não identifica condomínio")
    row = await conn.fetchrow(
        "select id, nome from condominios where ativo and slug = $1", slug
    )
    return CondominioElegivel.model_validate(dict(row)) if row else None


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


async def condominio_do_sindico(
    conn: asyncpg.Connection, telefone: str
) -> UUID | None:
    """O condomínio de quem é síndico por este número, ou None.

    fetchval basta: uq_condominios_sindico_telefone é único. Sem filtro de
    `ativo` — tenant desativado ainda tem síndico, e não reconhecê-lo o jogaria
    no fluxo de morador.
    """
    return await conn.fetchval(
        "select id from condominios where sindico_telefone = $1", telefone
    )


async def timezone_por_id(conn: asyncpg.Connection, condominio_id: UUID) -> str:
    """O fuso civil do condomínio — o que traduz "dia 25" em instantes.

    Coluna NOT NULL alcançada por FK: None aqui é constraint quebrada, não caso
    de uso. Adivinhar gravaria a reserva no dia errado.
    """
    tz = await conn.fetchval(
        "select timezone from condominios where id = $1", condominio_id
    )
    if tz is None:
        raise ValueError(f"condomínio {condominio_id} sem timezone")
    return tz
