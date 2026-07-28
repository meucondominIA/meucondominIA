"""Testes do processador (Fase 1 · Parte 2; Fase 3 · Passo 3).

Unitários: pool/repositório/atendimento/envio mockados no namespace do
processador — testamos a ORQUESTRAÇÃO (o quê chama o quê, em que ordem) e não a
decisão do atendimento, que tem testes próprios. A contingência usa renderizar
REAL (função pura), para o texto ser o de verdade.

Integração leve: POST real na rota do webhook com o processador REAL sobre conn
fake e envio mockado — prova entrada → decisão → saída → 'processado'.
"""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import processador
import webhook
from atendimento import GeracaoPendente
from config import settings
from dedup import StatusEvento
from main import app
from roteador import Conversa, Estado, Transicao
from textos import MensagemAtendimento, renderizar
from zpro_models import IncomingMessage, MessageType

CONVERSA_ID = uuid4()
ENTRADA_ID = uuid4()
CONDOMINIO_ID = uuid4()
_AGORA = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)

LINHA_CONVERSA = {
    "id": CONVERSA_ID,
    "estado": "identificacao",
    "condominio_id": None,
    "condominio_pendente": None,
    "ultima_interacao_em": _AGORA,
    "telefone": "5555999999999",
    "rascunho": None,
}
CONVERSA = Conversa(
    id=CONVERSA_ID,
    estado=Estado.IDENTIFICACAO,
    condominio_id=None,
    condominio_pendente=None,
    ultima_interacao_em=_AGORA,
    telefone="5555999999999",
    rascunho=None,
)

RESPOSTA = "Olá! Escolha o seu condomínio:\n\n1 - Edifício X"
GERADA = "Pode, um por unidade.\n\nFonte: regimento-art-10"
PENDENTE = GeracaoPendente(
    pergunta="Posso ter cachorro?", condominio_id=CONDOMINIO_ID, historico=[]
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

    async def fetch(self, query, *args):
        self.calls.append(("fetch", query, args))
        return []

    async def execute(self, query, *args):
        self.calls.append(("execute", query, args))


class _FakeAcquire:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        self._pool.em_uso = True
        return self._pool._conn

    async def __aexit__(self, *exc):
        self._pool.em_uso = False
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn
        self.em_uso = False

    def acquire(self):
        return _FakeAcquire(self)


@pytest.fixture
def deps(monkeypatch):
    """Mocka pool, repositório, atendimento e envio no namespace do processador."""
    conn = _FakeConn()
    mocks = SimpleNamespace(
        conn=conn,
        upsert=AsyncMock(return_value=(CONVERSA, False)),
        entrada=AsyncMock(return_value=(ENTRADA_ID, True)),
        ja_existe=AsyncMock(return_value=False),
        responder=AsyncMock(return_value=(RESPOSTA, None)),
        gerar=AsyncMock(return_value=GERADA),
        saida=AsyncMock(),
        transicao=AsyncMock(),
        interacao=AsyncMock(),
        enviar=AsyncMock(),
    )
    monkeypatch.setattr(processador, "get_pool", lambda: _FakePool(conn))
    monkeypatch.setattr(processador, "conversa_ativa", mocks.upsert)
    monkeypatch.setattr(processador, "registrar_entrada", mocks.entrada)
    monkeypatch.setattr(processador, "saida_ja_existe", mocks.ja_existe)
    monkeypatch.setattr(processador, "responder", mocks.responder)
    monkeypatch.setattr(processador, "responder_duvida", mocks.gerar)
    monkeypatch.setattr(processador, "registrar_saida", mocks.saida)
    monkeypatch.setattr(processador, "aplicar_transicao", mocks.transicao)
    monkeypatch.setattr(processador, "marcar_interacao", mocks.interacao)
    monkeypatch.setattr(processador, "enviar", mocks.enviar)
    return mocks


def test_fluxo_feliz_envia_a_decisao_e_grava_saida(deps):
    asyncio.run(processador.processar_mensagem(_msg("Oi")))

    out = deps.enviar.await_args.args[0]
    assert out.phone == "555592372732"
    assert out.text == RESPOSTA
    assert out.external_key == "MSG-1"

    deps.upsert.assert_awaited_once_with(deps.conn, "555592372732")
    deps.saida.assert_awaited_once_with(deps.conn, CONVERSA_ID, RESPOSTA, ENTRADA_ID)
    deps.interacao.assert_awaited_once_with(deps.conn, CONVERSA_ID)
    deps.ja_existe.assert_not_awaited()


def test_sem_transicao_nao_chama_aplicar(deps):
    """A maioria das respostas não muda de estado — não gasta um UPDATE à toa."""
    asyncio.run(processador.processar_mensagem(_msg("Oi")))
    deps.transicao.assert_not_awaited()


def test_com_transicao_grava_o_novo_estado(deps):
    trans = Transicao.para_menu(CONDOMINIO_ID)
    deps.responder.return_value = ("Menu…", trans)

    asyncio.run(processador.processar_mensagem(_msg("1")))

    deps.transicao.assert_awaited_once_with(deps.conn, CONVERSA_ID, trans)
    deps.interacao.assert_awaited_once_with(deps.conn, CONVERSA_ID)


def test_falha_no_atendimento_vira_contingencia(deps):
    """Falha ANTES do envio não é silêncio: o morador recebe a contingência, sem
    transição, e a saída é gravada. A exceção NÃO propaga (foi tratada)."""
    deps.responder.side_effect = Exception("boom no atendimento")

    asyncio.run(processador.processar_mensagem(_msg("Oi")))

    out = deps.enviar.await_args.args[0]
    assert out.text == renderizar(MensagemAtendimento.CONTINGENCIA)
    deps.transicao.assert_not_awaited()
    deps.saida.assert_awaited_once()
    deps.interacao.assert_awaited_once()


# ── dúvidas: a geração entre as janelas (Passo 7) ────────────────────────────


def test_geracao_pendente_gera_envia_e_grava(deps):
    deps.responder.return_value = PENDENTE

    asyncio.run(processador.processar_mensagem(_msg("Posso ter cachorro?")))

    deps.gerar.assert_awaited_once_with(
        PENDENTE.pergunta, PENDENTE.condominio_id, PENDENTE.historico
    )
    out = deps.enviar.await_args.args[0]
    assert out.text == GERADA
    deps.saida.assert_awaited_once_with(deps.conn, CONVERSA_ID, GERADA, ENTRADA_ID)
    deps.transicao.assert_not_awaited()


def test_geracao_roda_sem_conexao_presa(deps, monkeypatch):
    """A regra de ouro: quando responder_duvida roda, o pool já recebeu a
    conexão de volta."""
    pool = _FakePool(deps.conn)
    monkeypatch.setattr(processador, "get_pool", lambda: pool)
    deps.responder.return_value = PENDENTE
    livre = []

    async def _gerar(pergunta, condominio_id, historico):
        livre.append(not pool.em_uso)
        return GERADA

    monkeypatch.setattr(processador, "responder_duvida", _gerar)

    asyncio.run(processador.processar_mensagem(_msg("Posso ter cachorro?")))

    assert livre == [True]


def test_falha_na_geracao_vira_contingencia(deps):
    deps.responder.return_value = PENDENTE
    deps.gerar.side_effect = RuntimeError("bug na geração")

    asyncio.run(processador.processar_mensagem(_msg("Posso ter cachorro?")))

    out = deps.enviar.await_args.args[0]
    assert out.text == renderizar(MensagemAtendimento.CONTINGENCIA)
    deps.saida.assert_awaited_once()
    deps.transicao.assert_not_awaited()
    deps.interacao.assert_awaited_once()


def test_duplicata_com_saida_existente_nao_reenvia(deps):
    deps.entrada.return_value = (ENTRADA_ID, False)
    deps.ja_existe.return_value = True

    asyncio.run(processador.processar_mensagem(_msg("Oi")))

    deps.ja_existe.assert_awaited_once_with(deps.conn, ENTRADA_ID)
    deps.responder.assert_not_awaited()
    deps.enviar.assert_not_awaited()
    deps.saida.assert_not_awaited()


def test_duplicata_sem_saida_reenvia(deps):
    deps.entrada.return_value = (ENTRADA_ID, False)
    deps.ja_existe.return_value = False

    asyncio.run(processador.processar_mensagem(_msg("Oi")))

    deps.enviar.assert_awaited_once()
    deps.saida.assert_awaited_once()


def test_falha_no_envio_propaga_e_nao_grava_saida(deps):
    """A falha do próprio canal NÃO é contingência: sobe para marcar 'falhou'."""
    deps.enviar.side_effect = Exception("boom no envio")

    with pytest.raises(Exception, match="boom no envio"):
        asyncio.run(processador.processar_mensagem(_msg("Oi")))

    deps.saida.assert_not_awaited()
    deps.interacao.assert_not_awaited()


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
    """Webhook → processador REAL → atendimento REAL sobre conn fake.

    A conversa nasce em 'identificacao'; o atendimento lista condomínios (fetch
    devolve []), então a resposta é 'sem condomínios'. O foco é o ciclo, não o
    texto: entrada gravada, envio disparado, saída gravada, 'processado'.
    """
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
    assert out.external_key == "E2E-1"
    assert out.text == renderizar(MensagemAtendimento.SEM_CONDOMINIOS)

    inserts_mensagens = [
        (metodo, query, args)
        for metodo, query, args in conn.calls
        if "insert into mensagens" in query.lower()
    ]
    assert len(inserts_mensagens) == 2
    assert inserts_mensagens[0][2][1:] == ("text", "Oi", "E2E-1")
    assert inserts_mensagens[1][2] == (
        CONVERSA_ID,
        renderizar(MensagemAtendimento.SEM_CONDOMINIOS),
        ENTRADA_ID,
    )

    marcar.assert_awaited_once()
    assert marcar.await_args.args[1] == "E2E-1"
    assert marcar.await_args.args[2] is StatusEvento.PROCESSADO
