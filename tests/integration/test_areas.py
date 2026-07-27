"""Integração: listar_areas_reservaveis contra Postgres real (Fase 4 · Etapa 1).

Prova o que os fakes não pegam: o filtro de `reservavel`, o isolamento por
condomínio e a ordem alfabética que o wizard vai numerar.
"""

import pytest

from areas import listar_areas_reservaveis

pytestmark = pytest.mark.integration


async def _cond(conn, slug):
    return await conn.fetchval(
        "insert into condominios (slug, nome) values ($1, $2) returning id",
        slug,
        slug.upper(),
    )


async def _area(conn, cond, nome, *, reservavel=True):
    return await conn.fetchval(
        "insert into areas_comuns (condominio_id, nome, reservavel) "
        "values ($1, $2, $3) returning id",
        cond,
        nome,
        reservavel,
    )


def test_so_reservaveis_do_tenant_em_ordem(rodar_tx):
    async def body(conn):
        a = await _cond(conn, "cond-areas-a")
        b = await _cond(conn, "cond-areas-b")
        await _area(conn, a, "Salão de Festas")
        await _area(conn, a, "Churrasqueira")
        await _area(conn, a, "Depósito", reservavel=False)  # não reservável -> fora
        await _area(conn, b, "Piscina")  # outro condomínio -> fora

        areas = await listar_areas_reservaveis(conn, a)

        assert [x.nome for x in areas] == ["Churrasqueira", "Salão de Festas"]

    rodar_tx(body)


def test_condominio_sem_area_devolve_vazio(rodar_tx):
    async def body(conn):
        a = await _cond(conn, "cond-areas-vazio")
        assert await listar_areas_reservaveis(conn, a) == []

    rodar_tx(body)
