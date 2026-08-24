"""Testes do repositório de mensagens (Parte 2 · Peça 4).

Sem banco real (molde do test_sweeper): _FakeConn grava cada chamada e devolve
resultados programados. A semântica do ON CONFLICT é garantia do Postgres
(sql-insert); aqui testamos a orquestração — SQL certo, parâmetros na ordem
certa, caminho novo vs duplicata.
"""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

import mensagens
from roteador import Estado, Transicao
from zpro_models import IncomingMessage, MessageType

CONVERSA_ID = uuid4()
ENTRADA_ID = uuid4()
CONDOMINIO_ID = uuid4()

LINHA_CONVERSA = {
    "id": CONVERSA_ID,
    "estado": "identificacao",
    "condominio_id": None,
    "condominio_pendente": None,
    "ultima_interacao_em": datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc),
    "telefone": "555590000000",
    "rascunho": None,
}


def _msg(text: str | None = "Oi") -> IncomingMessage:
    return IncomingMessage(
        message_id="MSG-1",
        phone="555590000000",
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


class _FakeConn:
    def __init__(self, fetchrow_results=(), fetchval_result=None, fetch_result=()):
        self._fetchrow_results = list(fetchrow_results)
        self._fetchval_result = fetchval_result
        self._fetch_result = list(fetch_result)
        self.calls = []

    async def fetch(self, query, *args):
        self.calls.append(("fetch", query, args))
        return self._fetch_result

    async def fetchrow(self, query, *args):
        self.calls.append(("fetchrow", query, args))
        return self._fetchrow_results.pop(0)

    async def fetchval(self, query, *args):
        self.calls.append(("fetchval", query, args))
        return self._fetchval_result

    async def execute(self, query, *args):
        self.calls.append(("execute", query, args))


def test_conversa_ativa_existente_nao_e_nova():
    """Achou a ativa no SELECT: nem tenta inserir, e nova=False."""
    conn = _FakeConn(fetchrow_results=[LINHA_CONVERSA])
    conversa, nova = asyncio.run(mensagens.conversa_ativa(conn, "555590000000"))

    assert conversa.id == CONVERSA_ID
    assert conversa.estado is Estado.IDENTIFICACAO
    assert nova is False
    assert len(conn.calls) == 1
    _, query, args = conn.calls[0]
    assert "insert" not in query.lower()
    assert args == ("555590000000",)


def test_conversa_ativa_inexistente_abre_e_marca_nova():
    """Sem ativa, o INSERT abre outra e o `nova` habilita a pergunta certa."""
    conn = _FakeConn(fetchrow_results=[None, LINHA_CONVERSA])
    conversa, nova = asyncio.run(mensagens.conversa_ativa(conn, "555590000000"))

    assert conversa.id == CONVERSA_ID and nova is True
    _, query, _ = conn.calls[1]
    assert "insert into conversas" in query.lower()
    assert "do nothing" in query.lower()
    # a memória do telefone: o candidato sai do último condomínio CONFIRMADO
    assert "condominio_id is not null" in query.lower()
    # o RETURNING traz tudo junto: uma ida ao banco, não duas
    for coluna in ("estado", "condominio_pendente", "ultima_interacao_em", "rascunho"):
        assert coluna in query.lower().split("returning")[1]
    # ultima_interacao_em NÃO é tocada: o valor ANTIGO é o que mede a inatividade
    # — quem o avança é marcar_interacao, depois da resposta sair.
    assert "ultima_interacao_em = now()" not in query.lower()


def test_conversa_ativa_perde_a_corrida_e_relê():
    """Outra mensagem do mesmo telefone abriu entre o SELECT e o INSERT: o índice
    parcial decide o vencedor, o INSERT vira DO NOTHING e a gente relê."""
    conn = _FakeConn(fetchrow_results=[None, None, LINHA_CONVERSA])
    conversa, nova = asyncio.run(mensagens.conversa_ativa(conn, "555590000000"))

    assert conversa.id == CONVERSA_ID
    assert nova is False, "perder a corrida não é conversa nova"
    assert len(conn.calls) == 3


def test_marcar_interacao_avanca_a_sessao():
    conn = _FakeConn()
    asyncio.run(mensagens.marcar_interacao(conn, CONVERSA_ID))
    (metodo, query, args), *resto = conn.calls
    assert metodo == "execute"
    assert not resto
    assert "ultima_interacao_em = now()" in query.lower()
    assert args == (CONVERSA_ID,)


def test_aplicar_transicao_escreve_as_quatro_colunas_num_statement():
    conn = _FakeConn()
    asyncio.run(
        mensagens.aplicar_transicao(
            conn, CONVERSA_ID, Transicao.para_menu(CONDOMINIO_ID)
        )
    )
    (tipo, query, args), *resto = conn.calls
    assert tipo == "execute"
    assert not resto  # um único UPDATE — os CHECKs são sobre o conjunto
    for coluna in ("estado", "condominio_id", "condominio_pendente", "rascunho"):
        assert coluna in query
    assert args == (CONVERSA_ID, "menu", CONDOMINIO_ID, None, None)


def test_aplicar_transicao_para_identificacao_zera_os_dois_tenants():
    conn = _FakeConn()
    asyncio.run(
        mensagens.aplicar_transicao(conn, CONVERSA_ID, Transicao.para_identificacao())
    )
    _, _, args = conn.calls[0]
    assert args == (CONVERSA_ID, "identificacao", None, None, None)


def test_aplicar_transicao_grava_o_rascunho_junto_do_estado():
    """chk_conversas_rascunho recusa reserva sem sacola: as duas viajam juntas."""
    conn = _FakeConn()
    rascunho = {"passo": "area"}
    asyncio.run(
        mensagens.aplicar_transicao(
            conn, CONVERSA_ID, Transicao.para_reserva(CONDOMINIO_ID, rascunho)
        )
    )
    _, _, args = conn.calls[0]
    assert args == (CONVERSA_ID, "reserva", CONDOMINIO_ID, None, rascunho)


def test_registrar_entrada_nova():
    conn = _FakeConn(fetchrow_results=[{"id": ENTRADA_ID}])
    entrada_id, nova = asyncio.run(
        mensagens.registrar_entrada(conn, CONVERSA_ID, _msg())
    )
    assert (entrada_id, nova) == (ENTRADA_ID, True)
    assert len(conn.calls) == 1
    _, query, args = conn.calls[0]
    assert "on conflict (message_id) where message_id is not null" in query.lower()
    assert args == (CONVERSA_ID, "text", "Oi", "MSG-1")


def test_registrar_entrada_duplicata_busca_id_existente():
    conn = _FakeConn(fetchrow_results=[None, {"id": ENTRADA_ID}])
    entrada_id, nova = asyncio.run(
        mensagens.registrar_entrada(conn, CONVERSA_ID, _msg())
    )
    assert (entrada_id, nova) == (ENTRADA_ID, False)
    assert len(conn.calls) == 2
    _, query, args = conn.calls[1]
    assert query.lstrip().lower().startswith("select id from mensagens")
    assert args == ("MSG-1",)


def test_entrada_unsupported_grava_conteudo_nulo():
    conn = _FakeConn(fetchrow_results=[{"id": ENTRADA_ID}])
    asyncio.run(mensagens.registrar_entrada(conn, CONVERSA_ID, _msg(text=None)))
    _, _, args = conn.calls[0]
    assert args == (CONVERSA_ID, "unsupported", None, "MSG-1")


def test_saida_ja_existe_delega_ao_exists():
    conn = _FakeConn(fetchval_result=True)
    assert asyncio.run(mensagens.saida_ja_existe(conn, ENTRADA_ID)) is True
    _, query, args = conn.calls[0]
    assert "exists" in query.lower()
    assert args == (ENTRADA_ID,)


def test_registrar_saida_orquestra_parametros():
    conn = _FakeConn()
    asyncio.run(mensagens.registrar_saida(conn, CONVERSA_ID, "Eco: Oi", ENTRADA_ID))
    metodo, query, args = conn.calls[0]
    assert metodo == "execute"
    assert (
        "on conflict (em_resposta_a) where em_resposta_a is not null" in query.lower()
    )
    assert "'assistente'" in query
    assert args == (CONVERSA_ID, "Eco: Oi", ENTRADA_ID)


def test_ultimas_trocas_limite_invalido_levanta_sem_ir_ao_banco():
    conn = _FakeConn()
    with pytest.raises(ValueError):
        asyncio.run(mensagens.ultimas_trocas(conn, CONVERSA_ID, limite=0))
    assert conn.calls == []


def test_ultimas_trocas_inverte_para_ordem_cronologica():
    """O banco devolve desc (limit pega as mais novas); o chamador recebe asc."""
    linhas_desc = [
        {"pergunta": "E gato?", "resposta": "Também pode."},
        {"pergunta": "Pode cachorro?", "resposta": "Pode, um."},
    ]
    conn = _FakeConn(fetch_result=linhas_desc)
    trocas = asyncio.run(mensagens.ultimas_trocas(conn, CONVERSA_ID, limite=3))

    assert [(t.pergunta, t.resposta) for t in trocas] == [
        ("Pode cachorro?", "Pode, um."),
        ("E gato?", "Também pode."),
    ]
    _, query, args = conn.calls[0]
    assert "join mensagens entrada on entrada.id = saida.em_resposta_a" in query
    assert args == (CONVERSA_ID, 3)
