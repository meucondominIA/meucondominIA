"""Repositório de reservas — leitura e escrita (asyncpg; recebe conn, como regras.py).

A reserva nasce CONFIRMADA e o morador a desfaz sozinho; não há aprovação. O
valor gravado segue 'aprovada' porque é o que a excl_reservas_sem_conflito já
usa no predicado — trocá-lo custaria drop/recria da única garantia dura.

dias_livres responde "quais dias, na janela [de, ate], estão livres para a área".
Serve à lista de dias (janela larga) E ao dia digitado (janela de 1 dia).

O fuso é o nó. reservas.inicio/fim são timestamptz — instantes absolutos. "Dia 25
no condomínio" é civil, no fuso do prédio: vira o intervalo de instantes
[25 00:00 tz, 26 00:00 tz). `AT TIME ZONE` faz essa tradução (o fuso chega como
parâmetro, lido de condominios.timezone pelo chamador). "Ocupado" é sobreposição
de intervalos (tstzrange &&) — a MESMA língua da excl_reservas_sem_conflito, então
leitura e escrita concordam por construção, e uma reserva que cruza a meia-noite
ocupa os dois dias civis (a porta pro turno não fecha).

'pendente' segue nos dois filtros embora ninguém mais o produza: guarda
conservadora contra linha posta na mão. recusada/cancelada não ocupam — por isso
cancelar libera o dia sem que nada aqui mude.

Duas camadas, e elas concordam: o NOT EXISTS resolve o dia tomado há horas sem
gastar exceção; a exclusion constraint decide a corrida real e devolve 23P01 ao
chamador (READ COMMITTED não fecha isso sozinho).
"""

from datetime import date
from uuid import UUID

import asyncpg
from pydantic import BaseModel, ConfigDict

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


_CONFIRMAR = """
    insert into reservas (condominio_id, area_id, telefone, inicio, fim, status,
                          origem_mensagem_id)
    select $1::uuid, $2::uuid, $5::text,
           ($3::date)::timestamp at time zone $4::text,
           (($3::date) + 1)::timestamp at time zone $4::text,
           'aprovada', $6::uuid
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

_JA_CONFIRMADA = (
    "select id from reservas where origem_mensagem_id = $1 and status = 'aprovada'"
)


async def confirmar_reserva(
    conn: asyncpg.Connection,
    *,
    condominio_id: UUID,
    area_id: UUID,
    dia: date,
    tz: str,
    telefone: str,
    origem_mensagem_id: UUID,
) -> UUID | None:
    """Confirma o dia inteiro; None se o dia não estiver mais livre.

    O dia civil vira instantes no SQL, com a expressão de dias_livres — turno
    mudaria só essas duas linhas. Reprocessamento devolve o id já gravado: o gate
    é uq_reservas_origem_mensagem, quem decide a corrida é o banco.

    Corrida real levanta ExclusionViolationError (23P01) — o chamador traduz.
    O filtro de status no _JA_CONFIRMADA é o que impede devolver o id de uma
    reserva já cancelada quando a mensagem é reprocessada.
    unidade_id/morador_id nulos (identidade hoje é o telefone). Par área/tenant
    cruzado levanta ForeignKeyViolationError — é bug de chamador, não lotação.
    """
    row = await conn.fetchrow(
        _CONFIRMAR, condominio_id, area_id, dia, tz, telefone, origem_mensagem_id
    )
    if row is not None:
        return row["id"]
    return await conn.fetchval(_JA_CONFIRMADA, origem_mensagem_id)


class ReservaDoMorador(BaseModel):
    """Uma reserva viva do morador — o que a lista mostra e o cancelamento cita."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    area: str
    dia: date


_MINHAS = """
    select r.id, a.nome as area, (r.inicio at time zone $3::text)::date as dia
      from reservas r
      join areas_comuns a on a.id = r.area_id
     where r.telefone = $1
       and r.condominio_id = $2
       and r.status = 'aprovada'
       and r.fim > now()
     order by r.inicio, r.id
"""

_CANCELAR = """
    update reservas
       set status = 'cancelada'
     where id = $1
       and telefone = $2
       and condominio_id = $3
       and status = 'aprovada'
       and fim > now()
    returning id
"""


async def listar_minhas(
    conn: asyncpg.Connection, *, telefone: str, condominio_id: UUID, tz: str
) -> list[ReservaDoMorador]:
    """As reservas do morador que ainda não terminaram, na ordem em que ocorrem.

    Mesmo filtro do cancelamento: não existe reserva visível e incancelável.
    """
    rows = await conn.fetch(_MINHAS, telefone, condominio_id, tz)
    return [ReservaDoMorador.model_validate(dict(r)) for r in rows]


async def cancelar_reserva(
    conn: asyncpg.Connection, *, reserva_id: UUID, telefone: str, condominio_id: UUID
) -> UUID | None:
    """Cancela; None se a linha não é dele, não é deste tenant, já terminou ou já
    estava cancelada.

    As quatro guardas moram no WHERE, então o None é a idempotência: cancelar de
    novo não acha linha e o chamador não enfileira um segundo aviso.
    """
    return await conn.fetchval(_CANCELAR, reserva_id, telefone, condominio_id)
