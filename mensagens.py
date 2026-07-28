"""Repositório de conversas e mensagens (asyncpg; recebe conn, como dedup.py).

Os gates de idempotência moram nos índices únicos parciais criados na migration
(uq_conversas_telefone_ativa, uq_mensagens_message_id, uq_mensagens_em_resposta_a);
cada função aqui é um statement único que conversa com eles via ON CONFLICT —
o banco decide vencedor em corrida, não o Python.
"""

from uuid import UUID

import asyncpg

from contexto import Troca
from roteador import Conversa, Transicao
from zpro_models import IncomingMessage


_COLUNAS = """id, estado, condominio_id, condominio_pendente,
              ultima_interacao_em, telefone, rascunho"""

_ATIVA = f"""
    select {_COLUNAS}
      from conversas
     where telefone = $1 and status = 'ativa'
"""

# Conversa nova já nasce apontando para o último condomínio CONFIRMADO deste
# telefone: quem volta depois do encerramento ouve "É o Gabro?" em vez de refazer
# a lista. Sem memória (primeiro contato) nasce em 'identificacao', como antes.
_ABRIR = f"""
    with lembrado as (
        select condominio_id from conversas
         where telefone = $1 and condominio_id is not null
         order by ultima_interacao_em desc
         limit 1
    )
    insert into conversas (telefone, estado, condominio_pendente)
    select $1,
           case when (select condominio_id from lembrado) is null
                then 'identificacao' else 'aguardando_confirmacao' end,
           (select condominio_id from lembrado)
    on conflict (telefone) where status = 'ativa' do nothing
    returning {_COLUNAS}
"""


async def conversa_ativa(
    conn: asyncpg.Connection, telefone: str
) -> tuple[Conversa, bool]:
    """A conversa ativa do telefone, abrindo outra se a anterior foi encerrada.

    Devolve (conversa, nova). O `nova` não é conveniência: é o que permite ao
    atendimento PERGUNTAR "é o Gabro?" em vez de corrigir "só preciso de 1 ou 2"
    — o roteador não tem como saber que aquela conversa nunca fez a pergunta.

    SELECT antes de INSERT em vez de upsert: `on conflict do update` não diz se
    inseriu, e o truque do xmax não está na doc. Dois statements explícitos.

    `ultima_interacao_em` NÃO é tocada aqui de propósito: o valor ANTIGO é o que
    mede a inatividade. Quem a avança é marcar_interacao, depois da resposta sair.
    """
    row = await conn.fetchrow(_ATIVA, telefone)
    if row is not None:
        return Conversa.model_validate(dict(row)), False

    row = await conn.fetchrow(_ABRIR, telefone)
    if row is not None:
        return Conversa.model_validate(dict(row)), True

    # Corrida: outra mensagem do mesmo telefone abriu a conversa entre o SELECT e
    # o INSERT. O índice parcial decidiu o vencedor; aqui só lemos o resultado.
    row = await conn.fetchrow(_ATIVA, telefone)
    return Conversa.model_validate(dict(row)), False


async def marcar_interacao(conn: asyncpg.Connection, conversa_id: UUID) -> None:
    """Fecha a sessão desta mensagem. Separada de aplicar_transicao porque nem
    toda resposta transiciona, mas toda resposta é interação."""
    await conn.execute(
        "update conversas set ultima_interacao_em = now() where id = $1", conversa_id
    )


async def aplicar_transicao(
    conn: asyncpg.Connection, conversa_id: UUID, transicao: Transicao
) -> None:
    """Grava o destino numa instrução só.

    Não é estilo: chk_conversas_estado_coerente é sobre as três primeiras colunas
    e chk_conversas_rascunho amarra a quarta ao estado, então escrever só `estado`
    deixaria a linha incoerente no meio do caminho — e o banco recusaria. Um
    UPDATE, quatro colunas, os CHECKs validam o resultado.
    """
    await conn.execute(
        """
        update conversas
           set estado = $2, condominio_id = $3, condominio_pendente = $4,
               rascunho = $5
         where id = $1
        """,
        conversa_id,
        transicao.estado.value,
        transicao.condominio_id,
        transicao.condominio_pendente,
        transicao.rascunho,
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


async def ultimas_trocas(
    conn: asyncpg.Connection, conversa_id: UUID, *, limite: int
) -> list[Troca]:
    """As últimas `limite` trocas concluídas da conversa, em ordem cronológica.

    Troca é o par via em_resposta_a — a entrada recém-gravada ainda não tem
    resposta e fica de fora por construção. Pergunta NULL (áudio) não é troca
    utilizável; do lado da resposta o CHECK do banco garante conteúdo.
    """
    if limite < 1:
        raise ValueError(f"limite deve ser >= 1; recebi {limite}")
    rows = await conn.fetch(
        """
        select entrada.conteudo as pergunta, saida.conteudo as resposta
          from mensagens saida
          join mensagens entrada on entrada.id = saida.em_resposta_a
         where saida.conversa_id = $1
           and entrada.conteudo is not null
         order by saida.created_at desc
         limit $2
        """,
        conversa_id,
        limite,
    )
    return [Troca.model_validate(dict(row)) for row in reversed(rows)]
