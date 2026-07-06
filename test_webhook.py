"""Testes do endpoint de webhook (Fase 2.0 · Peça C).

Sem banco real: o pool e as funções de DB/processamento são substituídos por
fakes/mocks. O TestClient é usado SEM 'with' de propósito — assim o lifespan
(criar_pool) não dispara e não tentamos conectar no Postgres.

Doc TestClient: https://fastapi.tiangolo.com/reference/testclient/
"""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import webhook
from config import settings
from main import app

client = TestClient(app)


# --- fake do pool: get_pool().acquire() precisa ser um context manager assíncrono ---
class _FakeAcquire:
    async def __aenter__(self):
        return object()  # "conn" fake; registrar_mensagem é mockado, não o usa

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def acquire(self):
        return _FakeAcquire()


def _payload(*, msg_id="MSG-1", from_me=False, is_group=False, text="Oi"):
    return {
        "method": "message",
        "msg": {
            "key": {
                "id": msg_id,
                "fromMe": from_me,
                "sender_pn": "555592372732@s.whatsapp.net",
            },
            "messageTimestamp": 123,
            "pushName": "Lorenzo",
            "message": {"conversation": text},
        },
        "ticket": {
            "id": 1,
            "isGroup": is_group,
            "tenantId": 8,
            "whatsappId": 45,
            "contact": {"id": 1, "number": "555592372732", "name": "Lorenzo"},
            "whatsapp": {"id": 45, "type": "baileys"},
        },
    }


@pytest.fixture
def mocks(monkeypatch):
    """Substitui pool, dedup e processamento; devolve (registrar, processar)."""
    registrar = AsyncMock(return_value=True)
    processar = AsyncMock()
    monkeypatch.setattr(webhook, "get_pool", lambda: _FakePool())
    monkeypatch.setattr(webhook, "registrar_mensagem", registrar)
    monkeypatch.setattr(webhook, "processar_mensagem", processar)
    return registrar, processar


def test_segredo_errado_retorna_404(mocks):
    registrar, processar = mocks
    resp = client.post("/webhook/segredo-errado", json=_payload())
    assert resp.status_code == 404
    registrar.assert_not_awaited()
    processar.assert_not_awaited()


def test_mensagem_nova_processa(mocks):
    registrar, processar = mocks
    registrar.return_value = True  # não é duplicata
    resp = client.post(
        f"/webhook/{settings.webhook_secret}", json=_payload(msg_id="NOVA-1")
    )
    assert resp.status_code == 200
    registrar.assert_awaited_once()
    assert registrar.await_args.args[1] == "NOVA-1"  # (conn, message_id)
    processar.assert_awaited_once()
    assert processar.await_args.args[0].message_id == "NOVA-1"


def test_duplicata_nao_processa(mocks):
    registrar, processar = mocks
    registrar.return_value = False  # já existia -> ON CONFLICT DO NOTHING
    resp = client.post(
        f"/webhook/{settings.webhook_secret}", json=_payload(msg_id="DUP-1")
    )
    assert resp.status_code == 200
    registrar.assert_awaited_once()
    processar.assert_not_awaited()


def test_eco_fromme_ignorado(mocks):
    registrar, processar = mocks
    resp = client.post(
        f"/webhook/{settings.webhook_secret}", json=_payload(from_me=True)
    )
    assert resp.status_code == 200
    registrar.assert_not_awaited()  # parse levanta IgnoreMessage antes do dedup
    processar.assert_not_awaited()


def test_grupo_ignorado(mocks):
    registrar, processar = mocks
    resp = client.post(
        f"/webhook/{settings.webhook_secret}", json=_payload(is_group=True)
    )
    assert resp.status_code == 200
    registrar.assert_not_awaited()
    processar.assert_not_awaited()


def test_payload_invalido_nao_quebra(mocks):
    registrar, processar = mocks
    # method=message mas key.id ausente -> pydantic.ValidationError (tratada, vira 200).
    resp = client.post(
        f"/webhook/{settings.webhook_secret}",
        json={"method": "message", "msg": {"key": {}}},
    )
    assert resp.status_code == 200
    registrar.assert_not_awaited()
    processar.assert_not_awaited()
