"""Repositório de conversas e mensagens (asyncpg; recebe conn, como dedup.py).

Os gates de idempotência moram nos índices únicos parciais criados na migration
(uq_conversas_telefone_ativa, uq_mensagens_message_id, uq_mensagens_em_resposta_a);
cada função aqui é um statement único que conversa com eles via ON CONFLICT —
o banco decide vencedor em corrida, não o Python.
"""

from uuid import UUID

import asyncpg

from zpro_models import IncomingMessage


async def upsert_conversa_ativa(conn: asyncpg.Connection, telefone: str) -> UUID:
    """Devolve o id da conversa ativa do telefone, criando-a se não existir."""
    row = await conn.fetchrow(
        """
        insert into conversas (telefone)
        values ($1)
        on conflict (telefone) where status = 'ativa'
        do update set updated_at = now()
        returning id
        """,
        telefone,
    )
    return row["id"]


async def registrar_entrada(
    conn: asyncpg.Connection, conversa_id: UUID, msg: IncomingMessage
) -> tuple[UUID, bool]:
    """Grava a mensagem do morador; devolve (id, era_nova).

    Reentrega (reprocessamento do sweeper) conflita no índice parcial,
    não devolve linha e cai no SELECT do id existente.
    """
    row = await conn.fetchrow(
        """
        insert into mensagens (conversa_id, papel, tipo, conteudo, message_id)
        values ($1, 'morador', $2, $3, $4)
        on conflict (message_id) where message_id is not null
        do nothing
        returning id
        """,
        conversa_id,
        msg.message_type.value,
        msg.text,
        msg.message_id,
    )
    if row is not None:
        return row["id"], True

    row = await conn.fetchrow(
        "select id from mensagens where message_id = $1", msg.message_id
    )
    return row["id"], False


async def saida_ja_existe(conn: asyncpg.Connection, entrada_id: UUID) -> bool:
    """True se a entrada já tem resposta gravada (não reenviar o eco)."""
    return await conn.fetchval(
        "select exists (select 1 from mensagens where em_resposta_a = $1)",
        entrada_id,
    )


async def registrar_saida(
    conn: asyncpg.Connection, conversa_id: UUID, texto: str, entrada_id: UUID
) -> None:
    """Grava a resposta do assistente (message_id NULL — o Z-PRO não devolve id).

    Corrida de duas respostas para a mesma entrada morre no índice único:
    a segunda vira DO NOTHING em vez de violar a constraint.
    """
    await conn.execute(
        """
        insert into mensagens (conversa_id, papel, conteudo, em_resposta_a)
        values ($1, 'assistente', $2, $3)
        on conflict (em_resposta_a) where em_resposta_a is not null
        do nothing
        """,
        conversa_id,
        texto,
        entrada_id,
    )
