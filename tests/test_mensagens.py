"""Testes do repositório de mensagens (Parte 2 · Peça 4).

Sem banco real (molde do test_sweeper): _FakeConn grava cada chamada e devolve
resultados programados. A semântica do ON CONFLICT é garantia do Postgres
(sql-insert); aqui testamos a orquestração — SQL certo, parâmetros na ordem
certa, caminho novo vs duplicata.
"""

import asyncio
from uuid import uuid4

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
}


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


class _FakeConn:
    def __init__(self, fetchrow_results=(), fetchval_result=None):
        self._fetchrow_results = list(fetchrow_results)
        self._fetchval_result = fetchval_result
        self.calls = []

    async def fetchrow(self, query, *args):
        self.calls.append(("fetchrow", query, args))
        return self._fetchrow_results.pop(0)

    async def fetchval(self, query, *args):
        self.calls.append(("fetchval", query, args))
        return self._fetchval_result

    async def execute(self, query, *args):
        self.calls.append(("execute", query, args))


def test_upsert_conversa_ativa_devolve_a_trinca_de_estado():
    conn = _FakeConn(fetchrow_results=[LINHA_CONVERSA])
    conversa = asyncio.run(mensagens.upsert_conversa_ativa(conn, "555592372732"))
    assert conversa.id == CONVERSA_ID
    assert conversa.estado is Estado.IDENTIFICACAO
    assert conversa.condominio_id is None
    assert conversa.condominio_pendente is None
    _, query, args = conn.calls[0]
    assert "on conflict (telefone) where status = 'ativa'" in query.lower()
    assert "do update" in query.lower()
    # o RETURNING traz a trinca junto: uma ida ao banco, não duas
    assert "returning id, estado, condominio_id, condominio_pendente" in query.lower()
    assert args == ("555592372732",)


def test_aplicar_transicao_escreve_as_tres_colunas_num_statement():
    conn = _FakeConn()
    asyncio.run(
        mensagens.aplicar_transicao(
            conn, CONVERSA_ID, Transicao.para_menu(CONDOMINIO_ID)
        )
    )
    (tipo, query, args), *resto = conn.calls
    assert tipo == "execute"
    assert not resto  # um único UPDATE — o CHECK é sobre a trinca
    for coluna in ("estado", "condominio_id", "condominio_pendente"):
        assert coluna in query
    assert args == (CONVERSA_ID, "menu", CONDOMINIO_ID, None)


def test_aplicar_transicao_para_identificacao_zera_os_dois_tenants():
    conn = _FakeConn()
    asyncio.run(
        mensagens.aplicar_transicao(conn, CONVERSA_ID, Transicao.para_identificacao())
    )
    _, _, args = conn.calls[0]
    assert args == (CONVERSA_ID, "identificacao", None, None)


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
