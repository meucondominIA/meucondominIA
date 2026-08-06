"""Repositório do outbox de avisos ao síndico (asyncpg; recebe conn, como
solicitacoes.py).

Tabela em vez de `enviar()` direto porque o aviso é saída SEM entrada que a
ancore: `registrar_saida` grava DEPOIS do envio (recibo, não intenção) e
uq_mensagens_em_resposta_a é parcial — uma saída proativa cai fora do predicado
e o índice não protege nada.

O SQL do lote mora aqui, e não no job como em sweeper.py/faxina.py: ele É a
garantia (a lease), e separá-lo espalharia as invariantes da tabela.

Nada aqui faz rede.
"""

from enum import Enum
from uuid import UUID

import asyncpg
from pydantic import BaseModel, ConfigDict


class TipoAviso(str, Enum):
    """Espelha chk_avisos_sindico_tipo. Mudar aqui exige migration."""

    RESERVA_CRIADA = "reserva_criada"
    RESERVA_CANCELADA = "reserva_cancelada"
    OCORRENCIA_ABERTA = "ocorrencia_aberta"


_ENFILEIRAR_RESERVA = """
    insert into avisos_sindico (condominio_id, reserva_id, tipo, texto)
    values ($1, $2, $3, $4)
    on conflict (reserva_id, tipo) where reserva_id is not null
    do nothing
"""

_ENFILEIRAR_OCORRENCIA = """
    insert into avisos_sindico (condominio_id, solicitacao_id, tipo, texto)
    values ($1, $2, $3, $4)
    on conflict (solicitacao_id, tipo) where solicitacao_id is not null
    do nothing
"""

# Reserva o lote e devolve o que enviar, numa instrução só.
#
# lease: MEDIDO 29/07/2026 — sem ela dois workers enviam CADA aviso 2x. O lock do
#   FOR UPDATE cai no commit, e a regra de ouro exige commitar antes de enviar.
# `of a2`: sem o OF travaríamos condominios e serializaríamos tenants inteiros.
# 'pendente' literal: parâmetro não casa com índice parcial (como no sweeper).
# join por PK: o UPDATE ... FROM exige no máximo uma linha por linha atualizada.
# sindico_telefone not null: tenant sem síndico não entra no lote; o aviso fica
#   represado e sai sozinho quando o número for cadastrado.
_RESERVAR_LOTE = """
    with reservados as (
        update avisos_sindico a
           set reservado_ate = now() + make_interval(secs => $1),
               tentativas = tentativas + 1
          from condominios c
         where c.id = a.condominio_id
           and a.id in (
                 select a2.id
                   from avisos_sindico a2
                   join condominios c2 on c2.id = a2.condominio_id
                  where a2.status = 'pendente'
                    and (a2.reservado_ate is null or a2.reservado_ate < now())
                    and c2.sindico_telefone is not null
                  order by a2.created_at
                  limit $2
                    for update of a2 skip locked
               )
        returning a.id, a.texto, a.created_at, c.sindico_telefone as telefone
    )
    select id, texto, telefone from reservados order by created_at
"""

_MARCAR_ENVIADO = """
    update avisos_sindico
       set status = 'enviado', enviado_em = now(), reservado_ate = null
     where id = $1
"""


class AvisoPendente(BaseModel):
    """Uma linha reservada — só o que o envio precisa.

    O telefone vem do JOIN, não da linha: resolvido no envio, está sempre atual.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    texto: str
    telefone: str


async def enfileirar_aviso_reserva(
    conn: asyncpg.Connection, *, condominio_id: UUID, reserva_id: UUID, texto: str
) -> None:
    """Grava a intenção de avisar. Chamar na TX do pedido.

    Reprocessamento vira DO NOTHING no uq_avisos_sindico_reserva; o texto do
    primeiro aviso é preservado.
    """
    await conn.execute(
        _ENFILEIRAR_RESERVA,
        condominio_id,
        reserva_id,
        TipoAviso.RESERVA_CRIADA.value,
        texto,
    )


async def enfileirar_aviso_cancelamento(
    conn: asyncpg.Connection, *, condominio_id: UUID, reserva_id: UUID, texto: str
) -> None:
    """O segundo aviso da MESMA reserva. Chamar na TX do cancelamento."""
    await conn.execute(
        _ENFILEIRAR_RESERVA,
        condominio_id,
        reserva_id,
        TipoAviso.RESERVA_CANCELADA.value,
        texto,
    )


async def enfileirar_aviso_ocorrencia(
    conn: asyncpg.Connection, *, condominio_id: UUID, solicitacao_id: UUID, texto: str
) -> None:
    """Irmã da de reserva, no outro unique parcial."""
    await conn.execute(
        _ENFILEIRAR_OCORRENCIA,
        condominio_id,
        solicitacao_id,
        TipoAviso.OCORRENCIA_ABERTA.value,
        texto,
    )


async def reservar_lote(
    conn: asyncpg.Connection, *, lease_segundos: float, limite: int
) -> list[AvisoPendente]:
    """Reserva até `limite` avisos e devolve o que enviar.

    A transação do chamador tem que ser CURTA: o envio vem depois, sem conexão.
    """
    rows = await conn.fetch(_RESERVAR_LOTE, lease_segundos, limite)
    return [AvisoPendente.model_validate(dict(row)) for row in rows]


async def marcar_enviado(conn: asyncpg.Connection, aviso_id: UUID) -> None:
    """Fecha o aviso. Entre o envio e esta linha mora a única janela de
    duplicata — a lease expira e o próximo ciclo reenvia."""
    await conn.execute(_MARCAR_ENVIADO, aviso_id)
