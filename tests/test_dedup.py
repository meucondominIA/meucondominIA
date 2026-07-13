"""Testes do repositório da inbox (webhook_events).

Sem banco real (molde do test_mensagens): _FakeConn grava cada chamada e devolve
resultados programados. A atomicidade do ON CONFLICT é garantia do Postgres
(sql-insert); aqui testamos a orquestração — SQL certo, parâmetros na ordem
certa, novo (RETURNING traz linha) vs duplicata (não traz).
"""

import asyncio

import dedup
from dedup import StatusEvento

PAYLOAD = {"method": "message", "msg": {"key": {"id": "MSG-1"}}}


class _FakeConn:
    def __init__(self, fetchrow_result=None):
        self._fetchrow_result = fetchrow_result
        self.calls = []

    async def fetchrow(self, query, *args):
        self.calls.append(("fetchrow", query, args))
        return self._fetchrow_result

    async def execute(self, query, *args):
        self.calls.append(("execute", query, args))


def test_registrar_mensagem_nova_retorna_true():
    conn = _FakeConn(fetchrow_result={"message_id": "MSG-1"})
    novo = asyncio.run(dedup.registrar_mensagem(conn, "MSG-1", PAYLOAD))
    assert novo is True
    _, query, args = conn.calls[0]
    assert "insert into webhook_events" in query.lower()
    assert "on conflict (message_id) do nothing" in query.lower()
    assert "returning message_id" in query.lower()
    assert args == ("MSG-1", PAYLOAD)


def test_registrar_mensagem_duplicata_retorna_false():
    conn = _FakeConn(fetchrow_result=None)
    novo = asyncio.run(dedup.registrar_mensagem(conn, "MSG-1", PAYLOAD))
    assert novo is False


def test_marcar_status_monta_update():
    conn = _FakeConn()
    asyncio.run(dedup.marcar_status(conn, "MSG-1", StatusEvento.PROCESSADO))
    metodo, query, args = conn.calls[0]
    assert metodo == "execute"
    assert "update webhook_events" in query.lower()
    assert "processado_em = now()" in query.lower()
    assert args == ("processado", "MSG-1")


def test_marcar_status_usa_o_value_do_enum():
    conn = _FakeConn()
    asyncio.run(dedup.marcar_status(conn, "MSG-1", StatusEvento.FALHOU))
    _, _, args = conn.calls[0]
    assert args == ("falhou", "MSG-1")
