"""Testes do repositório de condomínios (Fase 2 · Passo 5; Fase 3 · Passo 3).

Molde do test_regras: _FakeConn grava as chamadas e devolve resultado
programado — aqui testamos o contrato (guarda antes da conn, SQL certo,
None repassado como None). O roundtrip real está na integração
(tests/integration/test_busca_slug.py e test_condominios_elegiveis.py).

O fake vê só o SQL que sai daqui. Que ele ordena igual nos dois provedores de
locale, só o Postgres real prova — na integração.
"""

import asyncio
from uuid import uuid4

import pytest

from condominios import (
    CondominioElegivel,
    buscar_id_por_slug,
    listar_elegiveis,
    nome_por_id,
)


class _FakeConn:
    def __init__(self, fetchval_result=None, fetch_result=(), fetchrow_result=None):
        self._fetchval_result = fetchval_result
        self._fetch_result = fetch_result
        self._fetchrow_result = fetchrow_result
        self.calls = []

    async def fetchval(self, query, *args):
        self.calls.append(("fetchval", query, args))
        return self._fetchval_result

    async def fetch(self, query, *args):
        self.calls.append(("fetch", query, args))
        return self._fetch_result

    async def fetchrow(self, query, *args):
        self.calls.append(("fetchrow", query, args))
        return self._fetchrow_result


def test_slug_em_branco_falha_antes_de_tocar_o_banco():
    conn = _FakeConn()
    with pytest.raises(ValueError, match="slug em branco"):
        asyncio.run(buscar_id_por_slug(conn, "   "))
    assert conn.calls == []


def test_busca_parametrizada_devolve_o_id():
    esperado = uuid4()
    conn = _FakeConn(fetchval_result=esperado)
    assert asyncio.run(buscar_id_por_slug(conn, "res-gabro")) == esperado
    [(tipo, query, args)] = conn.calls
    assert tipo == "fetchval"
    assert "where slug = $1" in query.lower()
    assert args == ("res-gabro",)


def test_slug_inexistente_devolve_none():
    conn = _FakeConn(fetchval_result=None)
    assert asyncio.run(buscar_id_por_slug(conn, "nao-existe")) is None


def test_slug_e_stripado_antes_da_consulta():
    conn = _FakeConn()
    asyncio.run(buscar_id_por_slug(conn, "  res-gabro  "))
    [(_, _, args)] = conn.calls
    assert args == ("res-gabro",)


# ── lista do morador (Fase 3 · Passo 3) ──────────────────────────────────────


def test_listagem_filtra_ativo_e_ordena_com_desempate():
    conn = _FakeConn(fetch_result=[])
    asyncio.run(listar_elegiveis(conn))
    [(tipo, query, args)] = conn.calls
    assert tipo == "fetch"
    assert args == ()

    sql = " ".join(query.lower().split())
    assert "where ativo" in sql
    assert 'order by nome collate "pt-br-x-icu", id' in sql


def test_listagem_devolve_modelos_na_ordem_do_banco():
    linhas = [
        {"id": uuid4(), "nome": "Edifício Alfa"},
        {"id": uuid4(), "nome": "Edifício Beta"},
    ]
    conn = _FakeConn(fetch_result=linhas)
    lista = asyncio.run(listar_elegiveis(conn))

    assert lista == [CondominioElegivel.model_validate(linha) for linha in linhas]
    assert [c.nome for c in lista] == ["Edifício Alfa", "Edifício Beta"]


def test_listagem_vazia_devolve_lista_vazia():
    assert asyncio.run(listar_elegiveis(_FakeConn(fetch_result=[]))) == []


def test_nome_por_id_e_parametrizado_e_sem_filtro_de_ativo():
    procurado = uuid4()
    conn = _FakeConn(fetchval_result="Edifício Alfa")
    assert asyncio.run(nome_por_id(conn, procurado)) == "Edifício Alfa"

    [(tipo, query, args)] = conn.calls
    assert tipo == "fetchval"
    assert "where id = $1" in query.lower()
    assert "ativo" not in query.lower()  # desativado ainda tem nome a citar
    assert args == (procurado,)


def test_condominio_elegivel_e_imutavel():
    condominio = CondominioElegivel(id=uuid4(), nome="Edifício Alfa")
    with pytest.raises(Exception):
        condominio.nome = "Outro"
