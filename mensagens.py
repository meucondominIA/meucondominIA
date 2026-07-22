"""Repositório de conversas e mensagens (asyncpg; recebe conn, como dedup.py).

Os gates de idempotência moram nos índices únicos parciais criados na migration
(uq_conversas_telefone_ativa, uq_mensagens_message_id, uq_mensagens_em_resposta_a);
cada função aqui é um statement único que conversa com eles via ON CONFLICT —
o banco decide vencedor em corrida, não o Python.
"""

from uuid import UUID

import asyncpg

from roteador import Conversa, Transicao
from zpro_models import IncomingMessage


async def upsert_conversa_ativa(conn: asyncpg.Connection, telefone: str) -> Conversa:
    """A conversa ativa do telefone, criando-a se não existir.

    `ultima_interacao_em` NÃO é tocada aqui de propósito: o RETURNING precisa
    devolver o valor ANTIGO, que é o que mede a inatividade. Quem a avança é
    marcar_interacao, depois da resposta sair.
    """
    row = await conn.fetchrow(
        """
        insert into conversas (telefone)
        values ($1)
        on conflict (telefone) where status = 'ativa'
        do update set updated_at = now()
        returning id, estado, condominio_id, condominio_pendente, ultima_interacao_em
        """,
        telefone,
    )
    return Conversa.model_validate(dict(row))


async def marcar_interacao(conn: asyncpg.Connection, conversa_id: UUID) -> None:
    """Fecha a sessão desta mensagem. Separada de aplicar_transicao porque nem
    toda resposta transiciona, mas toda resposta é interação."""
    await conn.execute(
        "update conversas set ultima_interacao_em = now() where id = $1", conversa_id
    )


async def aplicar_transicao(
    conn: asyncpg.Connection, conversa_id: UUID, transicao: Transicao
) -> None:
    """Grava a trinca numa instrução só.

    Não é estilo: chk_conversas_estado_coerente é sobre as TRÊS colunas, então
    escrever só `estado` deixaria a linha incoerente no meio do caminho — e o
    banco recusaria. Um UPDATE, três colunas, o CHECK valida o resultado.
    """
    await conn.execute(
        """
        update conversas
           set estado = $2, condominio_id = $3, condominio_pendente = $4
         where id = $1
        """,
        conversa_id,
        transicao.estado.value,
        transicao.condominio_id,
        transicao.condominio_pendente,
    )


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
