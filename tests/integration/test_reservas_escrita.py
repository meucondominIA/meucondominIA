"""Integração: criar_reserva_pendente contra Postgres real (Fase 4 · Etapa 2).

O que fake nenhum pega: a tradução do dia civil para instantes, o gate de
idempotência no índice único parcial, o NOT EXISTS que sustenta o D4 na escrita e
a FK composta que barra par área/tenant cruzado.
"""

from datetime import date

import asyncpg
import pytest

from reservas import criar_reserva_pendente

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


async def _mensagem(conn, telefone):
    """A mensagem de entrada que origina a reserva — a chave da idempotência."""
    conversa = await conn.fetchval(
        "insert into conversas (telefone) values ($1) returning id", telefone
    )
    return await conn.fetchval(
        "insert into mensagens (conversa_id, papel, tipo, conteudo, message_id) "
        "values ($1, 'morador', 'text', '1', $2) returning id",
        conversa,
        f"M-{telefone}",
    )


async def _aprovada(conn, cond, area, dia):
    """Aprovada entra DIRETO: aprovar_reserva é da Etapa 6."""
    await conn.execute(
        "insert into reservas (condominio_id, area_id, inicio, fim, status) values "
        "($1, $2, ($3::date)::timestamp at time zone $4, "
        "(($3::date) + 1)::timestamp at time zone $4, 'aprovada')",
        cond,
        area,
        dia,
        TZ,
    )


def test_grava_pendente_com_tenant_e_dia_civil_no_fuso(rodar_tx):
    """15/08 no condomínio = [15/08 00:00 -03, 16/08 00:00 -03) em timestamptz."""

    async def body(conn):
        c = await _cond(conn, "esc-cria")
        a = await _area(conn, c)
        m = await _mensagem(conn, "5555990001001")

        rid = await criar_reserva_pendente(
            conn,
            condominio_id=c,
            area_id=a,
            dia=DIA,
            tz=TZ,
            telefone="5555990001001",
            origem_mensagem_id=m,
        )

        row = await conn.fetchrow(
            "select condominio_id, area_id, telefone, status, unidade_id, morador_id,"
            "       to_char(inicio at time zone $2, 'YYYY-MM-DD HH24:MI') as ini,"
            "       to_char(fim at time zone $2, 'YYYY-MM-DD HH24:MI') as f"
            "  from reservas where id = $1",
            rid,
            TZ,
        )
        assert row["status"] == "pendente"
        assert (row["condominio_id"], row["area_id"]) == (c, a)
        assert row["telefone"] == "5555990001001"
        assert row["unidade_id"] is None and row["morador_id"] is None
        assert (row["ini"], row["f"]) == ("2026-08-15 00:00", "2026-08-16 00:00")

    rodar_tx(body)


def test_reprocessamento_devolve_o_mesmo_id_sem_duplicar(rodar_tx):
    """A janela real: crash entre esta escrita e a TX2 do processador."""

    async def body(conn):
        c = await _cond(conn, "esc-idem")
        a = await _area(conn, c)
        m = await _mensagem(conn, "5555990002002")
        pedido = dict(
            condominio_id=c,
            area_id=a,
            dia=DIA,
            tz=TZ,
            telefone="5555990002002",
            origem_mensagem_id=m,
        )

        primeiro = await criar_reserva_pendente(conn, **pedido)
        segundo = await criar_reserva_pendente(conn, **pedido)
        terceiro = await criar_reserva_pendente(conn, **pedido)

        gravadas = await conn.fetchval(
            "select count(*) from reservas where area_id = $1", a
        )
        assert primeiro is not None
        assert primeiro == segundo == terceiro
        assert gravadas == 1

    rodar_tx(body)


def test_dia_ocupado_por_pendente_ou_por_aprovada_devolve_none(rodar_tx):
    """D4 na escrita: as duas ocupam, e o segundo pedido não grava nada."""

    async def body(conn):
        c = await _cond(conn, "esc-ocupado")
        salao = await _area(conn, c, "Salão")
        churras = await _area(conn, c, "Churrasqueira")
        await criar_reserva_pendente(
            conn,
            condominio_id=c,
            area_id=salao,
            dia=DIA,
            tz=TZ,
            telefone="5555990003003",
            origem_mensagem_id=await _mensagem(conn, "5555990003003"),
        )
        await _aprovada(conn, c, churras, DIA)

        sobre_pendente = await criar_reserva_pendente(
            conn,
            condominio_id=c,
            area_id=salao,
            dia=DIA,
            tz=TZ,
            telefone="5555990003004",
            origem_mensagem_id=await _mensagem(conn, "5555990003004"),
        )
        sobre_aprovada = await criar_reserva_pendente(
            conn,
            condominio_id=c,
            area_id=churras,
            dia=DIA,
            tz=TZ,
            telefone="5555990003005",
            origem_mensagem_id=await _mensagem(conn, "5555990003005"),
        )

        assert sobre_pendente is None
        assert sobre_aprovada is None
        assert await conn.fetchval("select count(*) from reservas") == 2

    rodar_tx(body)


def test_dias_vizinhos_nao_colidem(rodar_tx):
    """'[)' com fim excluso: 15/08 ocupado não contamina 16/08."""

    async def body(conn):
        c = await _cond(conn, "esc-borda")
        a = await _area(conn, c)
        primeiro = await criar_reserva_pendente(
            conn,
            condominio_id=c,
            area_id=a,
            dia=DIA,
            tz=TZ,
            telefone="5555990004004",
            origem_mensagem_id=await _mensagem(conn, "5555990004004"),
        )
        seguinte = await criar_reserva_pendente(
            conn,
            condominio_id=c,
            area_id=a,
            dia=date(2026, 8, 16),
            tz=TZ,
            telefone="5555990004005",
            origem_mensagem_id=await _mensagem(conn, "5555990004005"),
        )

        assert primeiro is not None and seguinte is not None

    rodar_tx(body)


def test_mesmo_dia_em_condominios_diferentes_gravam_os_dois(rodar_tx):
    async def body(conn):
        ca = await _cond(conn, "esc-tenant-a")
        cb = await _cond(conn, "esc-tenant-b")
        aa = await _area(conn, ca)
        ab = await _area(conn, cb)

        na_a = await criar_reserva_pendente(
            conn,
            condominio_id=ca,
            area_id=aa,
            dia=DIA,
            tz=TZ,
            telefone="5555990005005",
            origem_mensagem_id=await _mensagem(conn, "5555990005005"),
        )
        na_b = await criar_reserva_pendente(
            conn,
            condominio_id=cb,
            area_id=ab,
            dia=DIA,
            tz=TZ,
            telefone="5555990005006",
            origem_mensagem_id=await _mensagem(conn, "5555990005006"),
        )

        assert na_a is not None and na_b is not None
        donos = await conn.fetch(
            "select condominio_id from reservas order by condominio_id"
        )
        assert sorted(r["condominio_id"] for r in donos) == sorted([ca, cb])

    rodar_tx(body)


def test_area_de_outro_condominio_e_erro_do_banco(rodar_tx):
    """fk_reservas_area_do_tenant: par cruzado não vira None silencioso."""

    async def body(conn):
        ca = await _cond(conn, "esc-cruz-a")
        cb = await _cond(conn, "esc-cruz-b")
        area_de_a = await _area(conn, ca)
        m = await _mensagem(conn, "5555990006006")

        with pytest.raises(asyncpg.ForeignKeyViolationError) as erro:
            async with conn.transaction():
                await criar_reserva_pendente(
                    conn,
                    condominio_id=cb,
                    area_id=area_de_a,
                    dia=DIA,
                    tz=TZ,
                    telefone="5555990006006",
                    origem_mensagem_id=m,
                )

        assert erro.value.sqlstate == "23503"
        assert erro.value.constraint_name == "fk_reservas_area_do_tenant"

    rodar_tx(body)
