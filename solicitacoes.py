"""Repositório de solicitações — a escrita da ocorrência (asyncpg; recebe conn,
como reservas.py).

Uma diferença de domínio, escrita no tipo: confirmar_reserva devolve
`UUID | None` porque o dia pode ter sido tomado no meio do caminho. A ocorrência
não tem estado de disponibilidade — é sempre criável —, então aqui é `UUID` e a
casca não ganha ramo de "não deu". O único caminho que não insere é o
reprocessamento, e esse tem um id para devolver.

O gate é uq_solicitacoes_origem_mensagem, espelhando uq_reservas_origem_mensagem:
a guarda saida_ja_existe do processador só passa a valer depois da TX2, e o crash
mora entre a escrita e ela.
"""

from uuid import UUID

import asyncpg

from ocorrencia import Anexo, TipoSolicitacao

_CRIAR = """
    insert into solicitacoes (condominio_id, tipo, descricao, anexos, telefone,
                              status, origem_mensagem_id)
    values ($1::uuid, $2::text, $3::text, $4::jsonb, $5::text, 'aberta', $6::uuid)
    on conflict (origem_mensagem_id) where origem_mensagem_id is not null
    do nothing
    returning id
"""

_JA_CRIADA = "select id from solicitacoes where origem_mensagem_id = $1"


async def criar_solicitacao(
    conn: asyncpg.Connection,
    *,
    condominio_id: UUID,
    tipo: TipoSolicitacao,
    descricao: str,
    anexos: list[Anexo],
    telefone: str,
    origem_mensagem_id: UUID,
) -> UUID:
    """Grava a ocorrência aberta; reprocessamento devolve o id já gravado.

    'aberta' vai escrito e não herdado do DEFAULT, como o 'pendente' de
    reservas.py: o significado do INSERT fica legível no próprio INSERT.
    unidade_id/morador_id nulos — identidade hoje é o telefone.

    `anexos` vai como lista Python: o codec de jsonb do db.py já é json.dumps, e
    serializar aqui gravaria a string do JSON dentro do JSON.
    """
    row = await conn.fetchrow(
        _CRIAR,
        condominio_id,
        tipo.value,
        descricao,
        [a.model_dump(mode="json") for a in anexos],
        telefone,
        origem_mensagem_id,
    )
    if row is not None:
        return row["id"]
    return await conn.fetchval(_JA_CRIADA, origem_mensagem_id)
