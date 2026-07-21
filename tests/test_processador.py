"""Testes do processador (Parte 2 · Peça 5).

Unitários: pool/repositório/envio mockados no namespace do processador —
testamos a orquestração (o quê chama o quê, com quais argumentos) e as
decisões (nova vs duplicata, texto do eco, falha propaga).

Integração leve: POST real na rota do webhook com o processador REAL rodando
sobre conn fake e envio mockado — prova o ciclo entrada → eco → saída →
'processado' sem banco nem rede.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import processador
import webhook
from config import settings
from dedup import StatusEvento
from main import app
from roteador import Conversa, Estado
from zpro_models import IncomingMessage, MessageType

CONVERSA_ID = uuid4()
ENTRADA_ID = uuid4()

LINHA_CONVERSA = {
    "id": CONVERSA_ID,
    "estado": "identificacao",
    "condominio_id": None,
    "condominio_pendente": None,
}
CONVERSA = Conversa(
    id=CONVERSA_ID,
    estado=Estado.IDENTIFICACAO,
    condominio_id=None,
    condominio_pendente=None,
)


def _msg(text: str | None = "Oi") -> IncomingMessage:
    return IncomingMessage(
        message_id="MSG-1",
        phone="555592372732",
        text=text,
        message_type=MessageType.TEXT if text else MessageType.UNSUPPORTED,
        push_name="Lorenzo",
        timestamp=123,
        zpro_ticket_id=1,
        zpro_whatsapp_id=45,
        zpro_tenant_id=8,
        channel_type="baileys",
        raw={},
    )


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, fetchrow_results=()):
        self._fetchrow_results = list(fetchrow_results)
        self.calls = []

    def transaction(self):
        return _FakeTransaction()

    async def fetchrow(self, query, *args):
        self.calls.append(("fetchrow", query, args))
        return self._fetchrow_results.pop(0)

    async def fetchval(self, query, *args):
        self.calls.append(("fetchval", query, args))
        return False

    async def execute(self, query, *args):
        self.calls.append(("execute", query, args))


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


@pytest.fixture
def deps(monkeypatch):
    """Mocka pool, repositório e envio no namespace do processador."""
    conn = _FakeConn()
    mocks = SimpleNamespace(
        conn=conn,
        upsert=AsyncMock(return_value=CONVERSA),
        entrada=AsyncMock(return_value=(ENTRADA_ID, True)),
        ja_existe=AsyncMock(return_value=False),
        saida=AsyncMock(),
        enviar=AsyncMock(),
    )
    monkeypatch.setattr(processador, "get_pool", lambda: _FakePool(conn))
    monkeypatch.setattr(processador, "upsert_conversa_ativa", mocks.upsert)
    monkeypatch.setattr(processador, "registrar_entrada", mocks.entrada)
    monkeypatch.setattr(processador, "saida_ja_existe", mocks.ja_existe)
    monkeypatch.setattr(processador, "registrar_saida", mocks.saida)
    monkeypatch.setattr(processador, "enviar", mocks.enviar)
    return mocks


def test_fluxo_feliz_envia_eco_e_grava_saida(deps):
    asyncio.run(processador.processar_mensagem(_msg("Oi")))

    out = deps.enviar.await_args.args[0]
    assert out.phone == "555592372732"
    assert out.text == "Eco: Oi"
    assert out.external_key == "MSG-1"

    deps.upsert.assert_awaited_once_with(deps.conn, "555592372732")
    deps.saida.assert_awaited_once_with(deps.conn, CONVERSA_ID, "Eco: Oi", ENTRADA_ID)
    deps.ja_existe.assert_not_awaited()


def test_unsupported_recebe_texto_padrao(deps):
    asyncio.run(processador.processar_mensagem(_msg(text=None)))
    out = deps.enviar.await_args.args[0]
    assert out.text == processador.TEXTO_UNSUPPORTED


def test_duplicata_com_saida_existente_nao_reenvia(deps):
    deps.entrada.return_value = (ENTRADA_ID, False)
    deps.ja_existe.return_value = True

    asyncio.run(processador.processar_mensagem(_msg("Oi")))

    deps.ja_existe.assert_awaited_once_with(deps.conn, ENTRADA_ID)
    deps.enviar.assert_not_awaited()
    deps.saida.assert_not_awaited()


def test_duplicata_sem_saida_reenvia(deps):
    deps.entrada.return_value = (ENTRADA_ID, False)
    deps.ja_existe.return_value = False

    asyncio.run(processador.processar_mensagem(_msg("Oi")))

    deps.enviar.assert_awaited_once()
    deps.saida.assert_awaited_once()


def test_falha_no_envio_propaga_e_nao_grava_saida(deps):
    deps.enviar.side_effect = Exception("boom no envio")

    with pytest.raises(Exception, match="boom no envio"):
        asyncio.run(processador.processar_mensagem(_msg("Oi")))

    deps.saida.assert_not_awaited()


def _payload(msg_id="E2E-1", text="Oi"):
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
            "message": {"conversation": text},
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


def test_ciclo_ponta_a_ponta(monkeypatch):
    conn = _FakeConn(fetchrow_results=[LINHA_CONVERSA, {"id": ENTRADA_ID}])
    enviar = AsyncMock()
    registrar = AsyncMock(return_value=True)
    marcar = AsyncMock()
    monkeypatch.setattr(processador, "get_pool", lambda: _FakePool(conn))
    monkeypatch.setattr(processador, "enviar", enviar)
    monkeypatch.setattr(webhook, "get_pool", lambda: _FakePool(conn))
    monkeypatch.setattr(webhook, "registrar_mensagem", registrar)
    monkeypatch.setattr(webhook, "marcar_status", marcar)

    client = TestClient(app)
    resp = client.post(f"/webhook/{settings.webhook_secret}", json=_payload())

    assert resp.status_code == 200

    out = enviar.await_args.args[0]
    assert out.text == "Eco: Oi"
    assert out.external_key == "E2E-1"

    inserts_mensagens = [
        (metodo, query, args)
        for metodo, query, args in conn.calls
        if "insert into mensagens" in query.lower()
    ]
    assert len(inserts_mensagens) == 2
    assert inserts_mensagens[0][2][1:] == ("text", "Oi", "E2E-1")
    assert inserts_mensagens[1][2] == (CONVERSA_ID, "Eco: Oi", ENTRADA_ID)

    marcar.assert_awaited_once()
    assert marcar.await_args.args[1] == "E2E-1"
    assert marcar.await_args.args[2] is StatusEvento.PROCESSADO
