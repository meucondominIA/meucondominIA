"""Repositório de reservas — LEITURA (asyncpg; recebe conn, como regras.py).

A escrita (criar_reserva_pendente, aprovar_reserva) e a prova da exclusion
constraint são da Etapa 2; aqui só a disponibilidade que o wizard consome.

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
