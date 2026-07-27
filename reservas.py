"""Repositório de reservas — leitura e escrita (asyncpg; recebe conn, como regras.py).

aprovar_reserva é da Etapa 6; aqui está a disponibilidade que o wizard consome e
a criação do pedido pendente.

dias_livres responde "quais dias, na janela [de, ate], estão livres para a área".
Serve à lista de dias (janela larga) E ao dia digitado (janela de 1 dia).

O fuso é o nó. reservas.inicio/fim são timestamptz — instantes absolutos. "Dia 25
no condomínio" é civil, no fuso do prédio: vira o intervalo de instantes
[25 00:00 tz, 26 00:00 tz). `AT TIME ZONE` faz essa tradução (o fuso chega como
parâmetro, lido de condominios.timezone pelo chamador). "Ocupado" é sobreposição
de intervalos (tstzrange &&) — a MESMA língua da excl_reservas_sem_conflito, então
leitura e escrita concordam por construção, e uma reserva que cruza a meia-noite
ocupa os dois dias civis (a porta pro turno não fecha).

D4: conta pendente E aprovada como ocupado (esconde da lista o dia já pedido).
A garantia dura do banco (a exclusion constraint) morde só em aprovada — aqui é a
política de LISTAGEM, mais estrita de propósito. recusada/cancelada não ocupam.

Três camadas, forças diferentes: dias_livres é foto do instante; o NOT EXISTS da
escrita fecha a janela do morador lento, mas não a corrida (READ COMMITTED); só a
exclusion constraint garante, e só entre aprovadas. Dois pendentes no mesmo dia
existem — a conta chega ao aprovar o segundo (23P01).
"""

from datetime import date
from uuid import UUID

import asyncpg

_DIAS_LIVRES = """
    select g.ts::date as dia
      from generate_series($3::timestamp, $4::timestamp, interval '1 day') as g(ts)
     where not exists (
             select 1
               from reservas r
              where r.condominio_id = $1
                and r.area_id = $2
                and r.status in ('pendente', 'aprovada')
                and tstzrange(r.inicio, r.fim, '[)') && tstzrange(
                      (g.ts::date)::timestamp at time zone $5,
                      (g.ts::date + 1)::timestamp at time zone $5,
                      '[)'
                    )
           )
     order by dia
"""


async def dias_livres(
    conn: asyncpg.Connection,
    *,
    condominio_id: UUID,
    area_id: UUID,
    de: date,
    ate: date,
    tz: str,
) -> list[date]:
    """Os dias livres da área na janela [de, ate], civil, no fuso do condomínio.

    Janela inclusiva nos dois extremos; `de == ate` responde por um dia só (o dia
    que o morador digitou).
    """
    rows = await conn.fetch(_DIAS_LIVRES, condominio_id, area_id, de, ate, tz)
    return [r["dia"] for r in rows]


_CRIAR_PENDENTE = """
    insert into reservas (condominio_id, area_id, telefone, inicio, fim, status,
                          origem_mensagem_id)
    select $1::uuid, $2::uuid, $5::text,
           ($3::date)::timestamp at time zone $4::text,
           (($3::date) + 1)::timestamp at time zone $4::text,
           'pendente', $6::uuid
     where not exists (
             select 1
               from reservas r
              where r.condominio_id = $1::uuid
                and r.area_id = $2::uuid
                and r.status in ('pendente', 'aprovada')
                and tstzrange(r.inicio, r.fim, '[)') && tstzrange(
                      ($3::date)::timestamp at time zone $4::text,
                      (($3::date) + 1)::timestamp at time zone $4::text,
                      '[)'
                    )
           )
    on conflict (origem_mensagem_id) where origem_mensagem_id is not null
    do nothing
    returning id
"""

_JA_CRIADA = "select id from reservas where origem_mensagem_id = $1"


async def criar_reserva_pendente(
    conn: asyncpg.Connection,
    *,
    condominio_id: UUID,
    area_id: UUID,
    dia: date,
    tz: str,
    telefone: str,
    origem_mensagem_id: UUID,
) -> UUID | None:
    """Grava o pedido do dia inteiro; None se o dia não estiver mais livre.

    O dia civil vira instantes no SQL, com a expressão de dias_livres — turno
    mudaria só essas duas linhas. Reprocessamento devolve o id já gravado: o gate
    é uq_reservas_origem_mensagem, quem decide a corrida é o banco.

    'pendente' e o alvo do ON CONFLICT vão escritos de propósito: é o que mantém
    este INSERT longe da exclusion constraint e impede engolir outro conflito.
    unidade_id/morador_id nulos (identidade hoje é o telefone). Par área/tenant
    cruzado levanta ForeignKeyViolationError — é bug de chamador, não lotação.
    """
    row = await conn.fetchrow(
        _CRIAR_PENDENTE, condominio_id, area_id, dia, tz, telefone, origem_mensagem_id
    )
    if row is not None:
        return row["id"]
    return await conn.fetchval(_JA_CRIADA, origem_mensagem_id)
