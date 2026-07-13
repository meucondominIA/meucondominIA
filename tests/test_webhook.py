"""Testes do endpoint de webhook (Fase 2.0 · Peça C).

Sem banco real: o pool e as funções de DB/processamento são substituídos por
fakes/mocks. O TestClient é usado SEM 'with' de propósito — assim o lifespan
(criar_pool) não dispara e não tentamos conectar no Postgres.

Doc TestClient: https://fastapi.tiangolo.com/reference/testclient/
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import webhook
from config import settings
from dedup import StatusEvento
from main import app
from zpro_models import parse_zpro_webhook

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
    """Substitui pool, dedup e processamento; devolve (registrar, processar, marcar)."""
    registrar = AsyncMock(return_value=True)
    processar = AsyncMock()
    marcar = AsyncMock()
    monkeypatch.setattr(webhook, "get_pool", lambda: _FakePool())
    monkeypatch.setattr(webhook, "registrar_mensagem", registrar)
    monkeypatch.setattr(webhook, "processar_mensagem", processar)
    monkeypatch.setattr(webhook, "marcar_status", marcar)
    return registrar, processar, marcar


def test_segredo_errado_retorna_404(mocks):
    registrar, processar, marcar = mocks
    resp = client.post("/webhook/segredo-errado", json=_payload())
    assert resp.status_code == 404
    registrar.assert_not_awaited()
    processar.assert_not_awaited()


def test_mensagem_nova_processa(mocks):
    registrar, processar, marcar = mocks
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
    registrar, processar, marcar = mocks
    registrar.return_value = False  # já existia -> ON CONFLICT DO NOTHING
    resp = client.post(
        f"/webhook/{settings.webhook_secret}", json=_payload(msg_id="DUP-1")
    )
    assert resp.status_code == 200
    registrar.assert_awaited_once()
    processar.assert_not_awaited()


def test_eco_fromme_ignorado(mocks):
    registrar, processar, marcar = mocks
    resp = client.post(
        f"/webhook/{settings.webhook_secret}", json=_payload(from_me=True)
    )
    assert resp.status_code == 200
    registrar.assert_not_awaited()  # parse levanta IgnoreMessage antes do dedup
    processar.assert_not_awaited()


def test_grupo_ignorado(mocks):
    registrar, processar, marcar = mocks
    resp = client.post(
        f"/webhook/{settings.webhook_secret}", json=_payload(is_group=True)
    )
    assert resp.status_code == 200
    registrar.assert_not_awaited()
    processar.assert_not_awaited()


def test_payload_invalido_nao_quebra(mocks):
    registrar, processar, marcar = mocks
    # method=message mas key.id ausente -> pydantic.ValidationError (tratada, vira 200).
    resp = client.post(
        f"/webhook/{settings.webhook_secret}",
        json={"method": "message", "msg": {"key": {}}},
    )
    assert resp.status_code == 200
    registrar.assert_not_awaited()
    processar.assert_not_awaited()


def test_json_invalido_ignorado(mocks):
    registrar, processar, marcar = mocks
    # corpo cru que não é JSON -> JSONDecodeError no parse -> 200 sem persistir.
    resp = client.post(
        f"/webhook/{settings.webhook_secret}", content=b"isso nao e json {{{"
    )
    assert resp.status_code == 200
    registrar.assert_not_awaited()
    processar.assert_not_awaited()


def test_evento_nao_message_ignorado(mocks):
    registrar, processar, marcar = mocks
    # JSON válido mas method != "message" -> IgnoreMessage -> 200 sem persistir.
    resp = client.post(
        f"/webhook/{settings.webhook_secret}", json={"method": "presence"}
    )
    assert resp.status_code == 200
    registrar.assert_not_awaited()
    processar.assert_not_awaited()


def test_falha_persistencia_retorna_503(mocks):
    registrar, processar, marcar = mocks
    # INSERT estoura -> a rota NÃO pode responder 2xx (o provedor precisa poder reenviar).
    registrar.side_effect = Exception("falha simulada no INSERT")
    resp = client.post(
        f"/webhook/{settings.webhook_secret}", json=_payload(msg_id="ERRO-1")
    )
    assert resp.status_code == 503
    registrar.assert_awaited_once()  # tentou persistir
    processar.assert_not_awaited()  # não agenda processamento sem durabilidade


def test_processamento_ok_marca_processado(mocks):
    registrar, processar, marcar = mocks
    resp = client.post(
        f"/webhook/{settings.webhook_secret}", json=_payload(msg_id="OK-1")
    )
    assert resp.status_code == 200
    processar.assert_awaited_once()
    marcar.assert_awaited_once()
    # marcar_status(conn, message_id, status)
    assert marcar.await_args.args[1] == "OK-1"
    assert marcar.await_args.args[2] is StatusEvento.PROCESSADO


def test_processamento_falha_marca_falhou(mocks):
    registrar, processar, marcar = mocks
    # Falha no background é PÓS-ACK: a resposta segue 200; a linha vira 'falhou'.
    processar.side_effect = Exception("boom no processamento")
    resp = client.post(
        f"/webhook/{settings.webhook_secret}", json=_payload(msg_id="FALHA-1")
    )
    assert resp.status_code == 200
    processar.assert_awaited_once()
    marcar.assert_awaited_once()
    assert marcar.await_args.args[2] is StatusEvento.FALHOU


def test_marcar_status_que_estoura_nao_propaga(mocks):
    # O desfecho já rodou; se marcar_status falha, é secundário -> só loga, não sobe.
    registrar, processar, marcar = mocks
    marcar.side_effect = Exception("falha ao marcar status")
    msg = parse_zpro_webhook(_payload(msg_id="MARCA-1"))

    asyncio.run(webhook._processar_e_marcar(msg))  # não deve levantar

    processar.assert_awaited_once()
    marcar.assert_awaited_once()
    assert marcar.await_args.args[2] is StatusEvento.PROCESSADO
