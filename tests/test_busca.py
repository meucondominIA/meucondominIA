"""Testes do serviço de busca (Fase 2 · Passo 6).

Unitários com fakes no namespace do módulo (padrão test_processador): o que
importa é a ORDEM — rede antes de qualquer conn — e o repasse fiel de
argumentos e resultados. A geometria real do <=> e o isolamento de tenant já
são provados na integração do Passo 4 (test_isolamento_tenant).
"""

import asyncio
from uuid import uuid4

import pytest

import busca
import embeddings
from config import settings
from regras import RegraEncontrada

CONDOMINIO_ID = uuid4()
VETOR = [0.1, 0.2]
REGRAS = [
    RegraEncontrada(conteudo="Art. 9º ...", fonte="Regimento, Art. 9º", distancia=0.3),
    RegraEncontrada(conteudo="Art. 1º ...", fonte="Regimento, Art. 1º", distancia=0.5),
]


@pytest.fixture
def harness(monkeypatch):
    eventos: list[str] = []
    chamadas: dict = {}

    async def gerar_embeddings(textos, *, caminho):
        eventos.append("rede")
        chamadas["textos"] = textos
        chamadas["caminho"] = caminho
        return [VETOR for _ in textos]

    class _Acquire:
        async def __aenter__(self):
            eventos.append("pool.acquire")
            return "conn-fake"

        async def __aexit__(self, *exc):
            eventos.append("pool.release")
            return False

    class _Pool:
        def acquire(self):
            return _Acquire()

    async def buscar_por_similaridade(conn, embedding, condominio_id, *, limite):
        eventos.append("select")
        chamadas["select"] = (conn, embedding, condominio_id, limite)
        return REGRAS

    monkeypatch.setattr(embeddings, "gerar_embeddings", gerar_embeddings)
    monkeypatch.setattr(busca, "get_pool", lambda: _Pool())
    monkeypatch.setattr(busca, "buscar_por_similaridade", buscar_por_similaridade)
    return eventos, chamadas


def test_ordem_rede_antes_da_conn_e_conn_curta(harness):
    eventos, _ = harness
    asyncio.run(busca.buscar_trechos("Pode ter cachorro?", CONDOMINIO_ID))
    assert eventos == ["rede", "pool.acquire", "select", "pool.release"]


def test_repassa_pergunta_caminho_conn_e_resultado_fielmente(harness):
    eventos, chamadas = harness
    resultado = asyncio.run(busca.buscar_trechos("Pode ter cachorro?", CONDOMINIO_ID))
    assert chamadas["textos"] == ["Pode ter cachorro?"]
    assert chamadas["caminho"] == "busca"
    assert chamadas["select"] == ("conn-fake", VETOR, CONDOMINIO_ID, settings.rag_top_k)
    assert resultado == REGRAS


def test_limite_explicito_sobrepoe_o_default(harness):
    _, chamadas = harness
    asyncio.run(busca.buscar_trechos("Pode ter cachorro?", CONDOMINIO_ID, limite=2))
    assert chamadas["select"][3] == 2


def test_pergunta_vazia_falha_no_adapter_antes_de_rede_e_conn(monkeypatch):
    eventos: list[str] = []

    def bomba():
        raise AssertionError("pergunta vazia tocou o pool")

    monkeypatch.setattr(busca, "get_pool", bomba)
    with pytest.raises(ValueError, match="texto vazio"):
        asyncio.run(busca.buscar_trechos("   ", CONDOMINIO_ID))
    assert eventos == []
