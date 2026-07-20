"""Testes do repositório de condomínios (Fase 2 · Passo 5).

Molde do test_regras: _FakeConn grava as chamadas e devolve resultado
programado — aqui testamos o contrato (guarda antes da conn, SQL certo,
None repassado como None). O roundtrip real está na integração
(tests/integration/test_busca_slug.py).
"""

import asyncio
from uuid import uuid4

import pytest

from condominios import buscar_id_por_slug


class _FakeConn:
    def __init__(self, fetchval_result=None):
        self._fetchval_result = fetchval_result
        self.calls = []

    async def fetchval(self, query, *args):
        self.calls.append(("fetchval", query, args))
        return self._fetchval_result


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
