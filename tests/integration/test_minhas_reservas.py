"""Integração: listar_minhas e cancelar_reserva contra Postgres real.

O que fake nenhum pega: o `fim > now()` medido contra o relógio do banco, o dia
civil saindo do fuso do condomínio, o isolamento por telefone E por tenant nas
quatro guardas do WHERE, e a idempotência que vem do UPDATE não achar linha.
"""

from datetime import date, timedelta

import pytest

from reservas import cancelar_reserva, confirmar_reserva, listar_minhas

pytestmark = pytest.mark.integration

TZ = "America/Sao_Paulo"
EU = "5555990030001"
OUTRO = "5555990030002"


async def _cond(conn, slug, tz=TZ):
    return await conn.fetchval(
        "insert into condominios (slug, nome, timezone) values ($1,$2,$3) returning id",
        slug,
        slug.upper(),
        tz,
    )


async def _area(conn, cond, nome="Salão de Festas"):
    return await conn.fetchval(
        "insert into areas_comuns (condominio_id, nome) values ($1,$2) returning id",
        cond,
        nome,
    )


async def _mensagem(conn, marca, telefone=EU):
    """Uma conversa por telefone: uq_conversas_telefone_ativa só admite uma ativa."""
    conversa = await conn.fetchval(
        "select id from conversas where telefone = $1 and status = 'ativa'", telefone
    ) or await conn.fetchval(
        "insert into conversas (telefone) values ($1) returning id", telefone
    )
    return await conn.fetchval(
        "insert into mensagens (conversa_id, papel, tipo, conteudo, message_id) "
        "values ($1,'morador','text','1',$2) returning id",
        conversa,
        marca,
    )


async def _hoje(conn, tz=TZ):
    return await conn.fetchval("select (now() at time zone $1)::date", tz)


async def _reservar(conn, cond, area, dia, telefone=EU, marca=None):
    return await confirmar_reserva(
        conn,
        condominio_id=cond,
        area_id=area,
        dia=dia,
        tz=TZ,
        telefone=telefone,
        origem_mensagem_id=await _mensagem(conn, marca or f"M-{telefone}-{dia}",
                                           telefone),
    )


def test_lista_so_o_que_e_meu_deste_tenant_e_ainda_nao_terminou(rodar_tx):
    async def corpo(conn):
        cond = await _cond(conn, "mr-lista")
        outro_cond = await _cond(conn, "mr-outro")
        area = await _area(conn, cond)
        area_outro = await _area(conn, outro_cond)
        hoje = await _hoje(conn)

        meu_depois = await _reservar(conn, cond, area, hoje + timedelta(days=5))
        meu_hoje = await _reservar(conn, cond, area, hoje)
        await _reservar(conn, cond, area, hoje - timedelta(days=1))
        await _reservar(conn, cond, area, hoje + timedelta(days=6), telefone=OUTRO)
        await _reservar(conn, outro_cond, area_outro, hoje + timedelta(days=7))

        minhas = await listar_minhas(conn, telefone=EU, condominio_id=cond, tz=TZ)

        assert [r.id for r in minhas] == [meu_hoje, meu_depois]
        assert [r.dia for r in minhas] == [hoje, hoje + timedelta(days=5)]
        assert {r.area for r in minhas} == {"Salão de Festas"}

    rodar_tx(corpo)


def test_o_dia_da_lista_e_civil_no_fuso_do_condominio(rodar_tx):
    """A reserva de 12/08 em Tóquio começa em 11/08 UTC; a lista mostra 12/08."""

    async def corpo(conn):
        cond = await _cond(conn, "mr-fuso", tz="Asia/Tokyo")
        area = await _area(conn, cond)
        dia = date(2027, 8, 12)
        await confirmar_reserva(
            conn,
            condominio_id=cond,
            area_id=area,
            dia=dia,
            tz="Asia/Tokyo",
            telefone=EU,
            origem_mensagem_id=await _mensagem(conn, "M-fuso"),
        )

        [minha] = await listar_minhas(
            conn, telefone=EU, condominio_id=cond, tz="Asia/Tokyo"
        )
        assert minha.dia == dia
        assert await conn.fetchval(
            "select (inicio at time zone 'UTC')::date from reservas where id = $1",
            minha.id,
        ) == date(2027, 8, 11)

    rodar_tx(corpo)


def test_lista_vazia_quando_nao_ha_nada_vivo(rodar_tx):
    async def corpo(conn):
        cond = await _cond(conn, "mr-vazia")
        area = await _area(conn, cond)
        hoje = await _hoje(conn)
        await _reservar(conn, cond, area, hoje - timedelta(days=2))

        assert await listar_minhas(conn, telefone=EU, condominio_id=cond, tz=TZ) == []

    rodar_tx(corpo)


def test_cancelar_libera_o_dia_e_some_da_lista(rodar_tx):
    async def corpo(conn):
        cond = await _cond(conn, "mr-cancela")
        area = await _area(conn, cond)
        hoje = await _hoje(conn)
        dia = hoje + timedelta(days=3)
        rid = await _reservar(conn, cond, area, dia)

        assert await cancelar_reserva(
            conn, reserva_id=rid, telefone=EU, condominio_id=cond
        ) == rid
        assert await listar_minhas(conn, telefone=EU, condominio_id=cond, tz=TZ) == []
        assert await _reservar(conn, cond, area, dia, telefone=OUTRO) is not None

    rodar_tx(corpo)


def test_cancelar_duas_vezes_a_segunda_devolve_none(rodar_tx):
    """A idempotência mora no WHERE: sem linha, o chamador não enfileira aviso."""

    async def corpo(conn):
        cond = await _cond(conn, "mr-idem")
        area = await _area(conn, cond)
        rid = await _reservar(conn, cond, area, await _hoje(conn) + timedelta(days=3))
        alvo = dict(reserva_id=rid, telefone=EU, condominio_id=cond)

        assert await cancelar_reserva(conn, **alvo) == rid
        assert await cancelar_reserva(conn, **alvo) is None

    rodar_tx(corpo)


def test_a_reserva_de_hoje_ainda_e_cancelavel(rodar_tx):
    """`fim > now()`: o dia em curso não terminou, e _janela já o oferta."""

    async def corpo(conn):
        cond = await _cond(conn, "mr-hoje")
        area = await _area(conn, cond)
        rid = await _reservar(conn, cond, area, await _hoje(conn))

        assert await cancelar_reserva(
            conn, reserva_id=rid, telefone=EU, condominio_id=cond
        ) == rid

    rodar_tx(corpo)


def test_reserva_terminada_nao_e_cancelavel(rodar_tx):
    async def corpo(conn):
        cond = await _cond(conn, "mr-passada")
        area = await _area(conn, cond)
        rid = await _reservar(conn, cond, area, await _hoje(conn) - timedelta(days=1))

        assert await cancelar_reserva(
            conn, reserva_id=rid, telefone=EU, condominio_id=cond
        ) is None
        assert await conn.fetchval(
            "select status from reservas where id = $1", rid
        ) == "aprovada"

    rodar_tx(corpo)


@pytest.mark.parametrize("quem", ["telefone alheio", "tenant alheio"])
def test_ninguem_cancela_reserva_que_nao_e_sua(rodar_tx, quem):
    async def corpo(conn):
        cond = await _cond(conn, "mr-isola")
        outro_cond = await _cond(conn, "mr-isola-b")
        area = await _area(conn, cond)
        rid = await _reservar(conn, cond, area, await _hoje(conn) + timedelta(days=4))

        alvo = (
            dict(telefone=OUTRO, condominio_id=cond)
            if quem == "telefone alheio"
            else dict(telefone=EU, condominio_id=outro_cond)
        )

        assert await cancelar_reserva(conn, reserva_id=rid, **alvo) is None
        assert await listar_minhas(conn, tz=TZ, **alvo) == []
        assert await conn.fetchval(
            "select status from reservas where id = $1", rid
        ) == "aprovada"

    rodar_tx(corpo)
