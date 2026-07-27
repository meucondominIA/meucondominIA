"""Integração: excl_reservas_sem_conflito no Postgres real (Fase 4 · Etapa 2).

A garantia dura da agenda, e o desenho da sua assimetria: a constraint é
WHERE status='aprovada', então dois PENDENTES no mesmo dia coexistem de
propósito — quem esconde isso do morador é a listagem (D4), não o banco.

Asserção em tipo e SQLSTATE, nunca em texto de mensagem. A violação vai dentro de
um savepoint (async with conn.transaction()) para não abortar a TX do rodar_tx.
"""

from datetime import date, datetime

import asyncpg
import pytest

pytestmark = pytest.mark.integration

TZ = "America/Sao_Paulo"
DIA = date(2026, 8, 15)


async def _cond(conn, slug):
    return await conn.fetchval(
        "insert into condominios (slug, nome) values ($1, $2) returning id",
        slug,
        slug.upper(),
    )


async def _area(conn, cond, nome="Salão de Festas"):
    return await conn.fetchval(
        "insert into areas_comuns (condominio_id, nome) values ($1, $2) returning id",
        cond,
        nome,
    )


async def _reserva(conn, cond, area, dia, status):
    return await conn.fetchval(
        "insert into reservas (condominio_id, area_id, inicio, fim, status) values "
        "($1, $2, ($3::date)::timestamp at time zone $5, "
        "(($3::date) + 1)::timestamp at time zone $5, $4) returning id",
        cond,
        area,
        dia,
        status,
        TZ,
    )


async def _faixa(conn, cond, area, inicio: datetime, fim: datetime, status="aprovada"):
    """Faixa de horas (hora civil ingênua + fuso), não o dia inteiro."""
    return await conn.execute(
        "insert into reservas (condominio_id, area_id, inicio, fim, status) values "
        "($1, $2, $3::timestamp at time zone $5, $4::timestamp at time zone $5, $6)",
        cond,
        area,
        inicio,
        fim,
        TZ,
        status,
    )


def test_segunda_aprovada_no_mesmo_slot_morre_no_banco(rodar_tx):
    async def body(conn):
        c = await _cond(conn, "excl-slot")
        a = await _area(conn, c)
        await _reserva(conn, c, a, DIA, "aprovada")

        with pytest.raises(asyncpg.ExclusionViolationError) as erro:
            async with conn.transaction():
                await _reserva(conn, c, a, DIA, "aprovada")

        assert erro.value.sqlstate == "23P01"
        assert erro.value.constraint_name == "excl_reservas_sem_conflito"

    rodar_tx(body)


def test_sobreposicao_parcial_conflita_e_encostada_nao(rodar_tx):
    """Não é só slot idêntico: 1h de encavalo já morde. Encostar no fim, não."""

    async def body(conn):
        c = await _cond(conn, "excl-parcial")
        a = await _area(conn, c)
        noite = datetime(2026, 8, 15, 18)
        fim_da_noite = datetime(2026, 8, 15, 23)
        madrugada = datetime(2026, 8, 16, 2)
        await _faixa(conn, c, a, noite, fim_da_noite)

        with pytest.raises(asyncpg.ExclusionViolationError):
            async with conn.transaction():
                await _faixa(conn, c, a, datetime(2026, 8, 15, 22), madrugada)

        await _faixa(conn, c, a, fim_da_noite, madrugada)
        assert await conn.fetchval("select count(*) from reservas") == 2

    rodar_tx(body)


def test_dois_pendentes_no_mesmo_dia_coexistem(rodar_tx):
    """A ASSIMETRIA, por escrito: o banco NÃO barra isto, e é intencional."""

    async def body(conn):
        c = await _cond(conn, "excl-assimetria")
        a = await _area(conn, c)

        await _reserva(conn, c, a, DIA, "pendente")
        await _reserva(conn, c, a, DIA, "pendente")

        assert await conn.fetchval("select count(*) from reservas") == 2

    rodar_tx(body)


def test_aprovar_o_segundo_pendente_e_que_estoura(rodar_tx):
    """A consequência da assimetria — a conta chega na Etapa 6, ao aprovar."""

    async def body(conn):
        c = await _cond(conn, "excl-aprovacao")
        a = await _area(conn, c)
        primeiro = await _reserva(conn, c, a, DIA, "pendente")
        segundo = await _reserva(conn, c, a, DIA, "pendente")

        await conn.execute(
            "update reservas set status = 'aprovada' where id = $1", primeiro
        )
        with pytest.raises(asyncpg.ExclusionViolationError) as erro:
            async with conn.transaction():
                await conn.execute(
                    "update reservas set status = 'aprovada' where id = $1", segundo
                )

        assert erro.value.sqlstate == "23P01"

    rodar_tx(body)


def test_recusada_e_cancelada_nao_ocupam(rodar_tx):
    """Fora do índice parcial: quantas quiser, e ainda cabe uma aprovada."""

    async def body(conn):
        c = await _cond(conn, "excl-liberadas")
        a = await _area(conn, c)

        await _reserva(conn, c, a, DIA, "recusada")
        await _reserva(conn, c, a, DIA, "cancelada")
        await _reserva(conn, c, a, DIA, "aprovada")

        assert await conn.fetchval("select count(*) from reservas") == 3

    rodar_tx(body)


def test_a_constraint_isola_por_area_e_por_condominio(rodar_tx):
    """Ela chaveia por area_id; o tenant vem junto porque a FK composta obriga."""

    async def body(conn):
        ca = await _cond(conn, "excl-iso-a")
        cb = await _cond(conn, "excl-iso-b")
        salao = await _area(conn, ca, "Salão")
        churras = await _area(conn, ca, "Churrasqueira")
        salao_de_b = await _area(conn, cb, "Salão")

        await _reserva(conn, ca, salao, DIA, "aprovada")
        await _reserva(conn, ca, churras, DIA, "aprovada")
        await _reserva(conn, cb, salao_de_b, DIA, "aprovada")

        assert await conn.fetchval("select count(*) from reservas") == 3

    rodar_tx(body)
