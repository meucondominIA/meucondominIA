"""Testes do sweeper (passo 2 · Peça 7).

Sem banco real: pool/conn/transaction são fakes. A semântica de FOR UPDATE
SKIP LOCKED é garantia do Postgres (doc do SELECT), não testável em unidade —
aqui testamos a orquestração: buscar lote -> reprocessar -> marcar desfecho.
Os testes são funções síncronas que rodam a corrotina com asyncio.run().
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

import sweeper
from config import settings
from dedup import StatusEvento


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.fetch_query = None
        self.fetch_args = None

    def transaction(self):
        return _FakeTransaction()

    async def fetch(self, query, *args):
        self.fetch_query = query
        self.fetch_args = args
        return self._rows


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _FakeAcquire(self._conn)


def _payload(msg_id="ORFA-1"):
    return {
        "method": "message",
        "msg": {
            "key": {
                "id": msg_id,
                "fromMe": False,
                "sender_pn": "555592372732@s.whatsapp.net",
            },
            "messageTimestamp": 123,
            "pushName": "Lorenzo",
            "message": {"conversation": "Oi"},
        },
        "ticket": {
            "id": 1,
            "isGroup": False,
            "tenantId": 8,
            "whatsappId": 45,
            "contact": {"id": 1, "number": "555592372732", "name": "Lorenzo"},
            "whatsapp": {"id": 45, "type": "baileys"},
        },
    }


@pytest.fixture
def mocks(monkeypatch):
    """Substitui processamento e marcação; devolve (processar, marcar)."""
    processar = AsyncMock()
    marcar = AsyncMock()
    monkeypatch.setattr(sweeper, "processar_mensagem", processar)
    monkeypatch.setattr(sweeper, "marcar_status", marcar)
    return processar, marcar


def _varrer(monkeypatch, rows):
    conn = _FakeConn(rows)
    monkeypatch.setattr(sweeper, "get_pool", lambda: _FakePool(conn))
    tratadas = asyncio.run(sweeper.varrer_pendentes())
    return conn, tratadas


def test_ciclo_sem_orfas(monkeypatch, mocks):
    processar, marcar = mocks
    conn, tratadas = _varrer(monkeypatch, rows=[])
    assert tratadas == 0
    processar.assert_not_awaited()
    marcar.assert_not_awaited()


def test_recupera_orfa_marca_processado(monkeypatch, mocks):
    processar, marcar = mocks
    conn, tratadas = _varrer(
        monkeypatch, rows=[{"message_id": "ORFA-1", "payload": _payload("ORFA-1")}]
    )
    assert tratadas == 1
    processar.assert_awaited_once()
    assert processar.await_args.args[0].message_id == "ORFA-1"
    marcar.assert_awaited_once()
    # marcar_status(conn, message_id, status) — na MESMA conn da transação
    assert marcar.await_args.args[0] is conn
    assert marcar.await_args.args[1] == "ORFA-1"
    assert marcar.await_args.args[2] is StatusEvento.PROCESSADO


def test_processamento_falha_marca_falhou(monkeypatch, mocks):
    processar, marcar = mocks
    processar.side_effect = Exception("boom no reprocessamento")
    conn, tratadas = _varrer(
        monkeypatch, rows=[{"message_id": "ORFA-2", "payload": _payload("ORFA-2")}]
    )
    assert tratadas == 1
    marcar.assert_awaited_once()
    assert marcar.await_args.args[2] is StatusEvento.FALHOU


def test_payload_indecifravel_marca_falhou(monkeypatch, mocks):
    # Não deveria existir (a borda filtrou no ingest), mas o sweeper não pode
    # nem quebrar nem devolver a linha ao limbo: marca 'falhou' e segue.
    processar, marcar = mocks
    conn, tratadas = _varrer(
        monkeypatch,
        rows=[
            {"message_id": "LIXO-1", "payload": {}},  # ValidationError
            {
                "message_id": "LIXO-2",
                "payload": {"method": "presence"},
            },  # IgnoreMessage
        ],
    )
    assert tratadas == 2
    processar.assert_not_awaited()
    assert marcar.await_count == 2
    assert all(c.args[2] is StatusEvento.FALHOU for c in marcar.await_args_list)


def test_falha_em_uma_nao_derruba_o_lote(monkeypatch, mocks):
    processar, marcar = mocks
    processar.side_effect = [Exception("boom"), None]
    conn, tratadas = _varrer(
        monkeypatch,
        rows=[
            {"message_id": "ORFA-3", "payload": _payload("ORFA-3")},
            {"message_id": "ORFA-4", "payload": _payload("ORFA-4")},
        ],
    )
    assert tratadas == 2
    desfechos = [c.args[2] for c in marcar.await_args_list]
    assert desfechos == [StatusEvento.FALHOU, StatusEvento.PROCESSADO]


def test_parametros_da_busca(monkeypatch, mocks):
    processar, marcar = mocks
    conn, _ = _varrer(monkeypatch, rows=[])
    sql = conn.fetch_query.lower()
    # status como LITERAL: índice parcial não casa com parâmetro (indexes-partial).
    assert "status = 'pendente'" in sql
    assert "for update skip locked" in sql
    assert conn.fetch_args == (
        float(settings.sweeper_grace_seconds),
        settings.sweeper_batch_size,
    )


def test_rodar_sweeper_continua_apos_erro_em_um_ciclo(monkeypatch):
    varrer = AsyncMock(side_effect=[Exception("boom no ciclo"), 0])

    async def fake_sleep(_):
        if varrer.await_count >= 2:
            raise asyncio.CancelledError  # encerra o loop no 2º ciclo

    monkeypatch.setattr(sweeper, "varrer_pendentes", varrer)
    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(sweeper.rodar_sweeper())
    assert varrer.await_count == 2  # o erro no 1º ciclo não derrubou o loop


def test_rodar_sweeper_encerra_no_cancelamento(monkeypatch):
    varrer = AsyncMock(return_value=0)

    async def fake_sleep(_):
        raise asyncio.CancelledError  # simula o cancel do shutdown

    monkeypatch.setattr(sweeper, "varrer_pendentes", varrer)
    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(sweeper.rodar_sweeper())
    varrer.assert_awaited_once()
