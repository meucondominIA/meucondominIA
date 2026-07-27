"""Integração: dias_livres contra Postgres real (Fase 4 · Etapa 1).

Datas civis no fuso do condomínio; reservas gravadas como timestamptz via
AT TIME ZONE (o mesmo caminho da escrita da Etapa 2). Fuso do piloto:
America/Sao_Paulo.
"""

from datetime import date

import pytest

from reservas import dias_livres

pytestmark = pytest.mark.integration

TZ = "America/Sao_Paulo"


async def _cond(conn, slug):
    return await conn.fetchval(
        "insert into condominios (slug, nome) values ($1, $2) returning id",
        slug,
        slug.upper(),
    )


async def _area(conn, cond, nome="Salão"):
    return await conn.fetchval(
        "insert into areas_comuns (condominio_id, nome) values ($1, $2) returning id",
        cond,
        nome,
    )


async def _reserva_dia(conn, cond, area, dia: date, status):
    """Grava a reserva do DIA inteiro [dia 00:00 tz, dia+1 00:00 tz)."""
    await conn.execute(
        """
        insert into reservas (condominio_id, area_id, inicio, fim, status)
        values ($1, $2,
                ($3::date)::timestamp at time zone $5,
                (($3::date) + 1)::timestamp at time zone $5,
                $4)
        """,
        cond,
        area,
        dia,
        status,
        TZ,
    )


def test_pendente_e_aprovada_ocupam_recusada_e_cancelada_liberam(rodar_tx):
    """D4 na prática: pendente já esconde o dia; recusada/cancelada devolvem."""

    async def body(conn):
        c = await _cond(conn, "cond-dias-a")
        a = await _area(conn, c)
        await _reserva_dia(conn, c, a, date(2026, 7, 20), "aprovada")
        await _reserva_dia(conn, c, a, date(2026, 7, 21), "pendente")
        await _reserva_dia(conn, c, a, date(2026, 7, 22), "recusada")
        await _reserva_dia(conn, c, a, date(2026, 7, 23), "cancelada")

        livres = await dias_livres(
            conn,
            condominio_id=c,
            area_id=a,
            de=date(2026, 7, 20),
            ate=date(2026, 7, 23),
            tz=TZ,
        )

        assert livres == [date(2026, 7, 22), date(2026, 7, 23)]

    rodar_tx(body)


def test_isolamento_por_area_e_por_condominio(rodar_tx):
    """Ocupação de OUTRA área ou de OUTRO condomínio não tira o dia do salão."""

    async def body(conn):
        c = await _cond(conn, "cond-dias-b")
        salao = await _area(conn, c, "Salão")
        churras = await _area(conn, c, "Churrasqueira")
        outro = await _cond(conn, "cond-dias-c")
        salao_outro = await _area(conn, outro, "Salão")

        await _reserva_dia(conn, c, churras, date(2026, 7, 20), "aprovada")
        await _reserva_dia(conn, outro, salao_outro, date(2026, 7, 20), "aprovada")

        livres = await dias_livres(
            conn,
            condominio_id=c,
            area_id=salao,
            de=date(2026, 7, 20),
            ate=date(2026, 7, 20),
            tz=TZ,
        )

        assert livres == [date(2026, 7, 20)]

    rodar_tx(body)


def test_janela_de_um_dia_o_dia_digitado(rodar_tx):
    """de == ate: valida o dia que o morador digitou (ocupado -> vazio; livre -> ele)."""

    async def body(conn):
        c = await _cond(conn, "cond-dias-d")
        a = await _area(conn, c)
        await _reserva_dia(conn, c, a, date(2026, 7, 25), "aprovada")

        ocupado = await dias_livres(
            conn, condominio_id=c, area_id=a,
            de=date(2026, 7, 25), ate=date(2026, 7, 25), tz=TZ,
        )
        livre = await dias_livres(
            conn, condominio_id=c, area_id=a,
            de=date(2026, 7, 26), ate=date(2026, 7, 26), tz=TZ,
        )

        assert ocupado == []
        assert livre == [date(2026, 7, 26)]

    rodar_tx(body)


def test_reserva_que_cruza_meia_noite_ocupa_os_dois_dias(rodar_tx):
    """Festa 22h->02h (fuso do condomínio) toca dois dias civis: os dois somem.
    Prova que a sobreposição de intervalos não assume reserva de dia inteiro."""

    async def body(conn):
        c = await _cond(conn, "cond-dias-e")
        a = await _area(conn, c)
        await conn.execute(
            """
            insert into reservas (condominio_id, area_id, inicio, fim, status)
            values ($1, $2,
                    '2026-07-20 22:00'::timestamp at time zone $3,
                    '2026-07-21 02:00'::timestamp at time zone $3,
                    'aprovada')
            """,
            c,
            a,
            TZ,
        )

        livres = await dias_livres(
            conn,
            condominio_id=c,
            area_id=a,
            de=date(2026, 7, 19),
            ate=date(2026, 7, 22),
            tz=TZ,
        )

        assert livres == [date(2026, 7, 19), date(2026, 7, 22)]

    rodar_tx(body)
