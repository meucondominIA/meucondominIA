"""Testes da costura roteador ↔ banco (Fase 3 · Passo 3).

_FakeConn programado: listar_elegiveis vira `fetch`, nome_por_id vira `fetchval`.
Provamos que a delegação do roteador vira a consulta certa, que a transição do
roteador passa intacta, e — o que importa de verdade — que o índice N indexa a
lista DEVOLVIDA, não uma segunda consulta.

A janela de sessão é testada com relógio real: ultima_interacao_em bem no
passado (expira) e agora (não expira). O corte exato (24h) é do config e não se
testa aqui — o que importa é a direção da decisão.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4


import atendimento
from atendimento import GeracaoPendente
from contexto import MAX_TROCAS, Troca
from roteador import Conversa, Estado, Transicao
from zpro_models import MessageType

C1, C2, C3 = uuid4(), uuid4(), uuid4()
CONVERSA_ID = uuid4()


class _FakeConn:
    """fetch devolve as linhas da lista; fetchval devolve o nome por id."""

    def __init__(self, lista=(), nomes=None):
        self._lista = list(lista)
        self._nomes = nomes or {}
        self.calls = []

    async def fetch(self, query, *args):
        self.calls.append(("fetch", query, args))
        return [{"id": cid, "nome": nome} for cid, nome in self._lista]

    async def fetchval(self, query, *args):
        self.calls.append(("fetchval", query, args))
        return self._nomes.get(args[0])


def _conversa(estado, *, condominio=None, pendente=None, idade_horas=0.0):
    return Conversa(
        id=CONVERSA_ID,
        estado=estado,
        condominio_id=condominio,
        condominio_pendente=pendente,
        ultima_interacao_em=datetime.now(timezone.utc)
        - timedelta(hours=idade_horas),
    )


def _responder(conn, conversa, texto, tipo=MessageType.TEXT):
    return asyncio.run(
        atendimento.responder(conn, conversa, tipo=tipo, texto=texto)
    )


LISTA3 = [(C1, "Alfa"), (C2, "Beta"), (C3, "Gama")]


# ── identificação: o índice indexa a lista devolvida ─────────────────────────


def test_indice_resolve_para_o_item_certo_da_lista():
    conn = _FakeConn(lista=LISTA3)
    texto, transicao = _responder(conn, _conversa(Estado.IDENTIFICACAO), "2")

    assert "Beta" in texto
    assert transicao == Transicao.para_confirmacao(C2)


def test_indice_alem_do_teto_pede_de_novo_sem_transicionar():
    conn = _FakeConn(lista=LISTA3)
    texto, transicao = _responder(conn, _conversa(Estado.IDENTIFICACAO), "9")

    assert transicao is None
    assert "Alfa" in texto and "Beta" in texto and "Gama" in texto


def test_lista_vazia_na_identificacao_avisa_sem_condominios():
    conn = _FakeConn(lista=[])
    texto, transicao = _responder(conn, _conversa(Estado.IDENTIFICACAO), "1")

    assert "não encontrei" in texto.lower()
    assert transicao is None


def test_uma_unica_consulta_de_lista_por_identificacao():
    """A invariante §3.5: mostrar e resolver leem a MESMA lista, não duas."""
    conn = _FakeConn(lista=LISTA3)
    _responder(conn, _conversa(Estado.IDENTIFICACAO), "2")

    fetches = [c for c in conn.calls if c[0] == "fetch"]
    assert len(fetches) == 1


# ── confirmação: a transição do roteador passa intacta ───────────────────────


def test_confirmacao_sim_vai_ao_menu():
    conn = _FakeConn()
    conversa = _conversa(Estado.AGUARDANDO_CONFIRMACAO, pendente=C1)
    texto, transicao = _responder(conn, conversa, "1")

    assert transicao == Transicao.para_menu(C1)
    assert "Como posso ajudar" in texto


def test_confirmacao_nao_entendida_cita_o_nome_do_pendente():
    conn = _FakeConn(nomes={C1: "Alfa"})
    conversa = _conversa(Estado.AGUARDANDO_CONFIRMACAO, pendente=C1)
    texto, transicao = _responder(conn, conversa, "talvez")

    assert "Alfa" in texto
    assert transicao is None
    assert conn.calls[-1] == ("fetchval", conn.calls[-1][1], (C1,))


# ── dúvidas: a delegação vira pacote de geração ──────────────────────────────


def test_duvida_vira_geracao_pendente_com_historico(monkeypatch):
    trocas = [Troca(pergunta="Pode festa?", resposta="Até 22h.")]
    chamadas = []

    async def _trocas(conn, conversa_id, *, limite):
        chamadas.append((conn, conversa_id, limite))
        return trocas

    monkeypatch.setattr(atendimento, "ultimas_trocas", _trocas)
    conn = _FakeConn()
    conversa = _conversa(Estado.DUVIDAS, condominio=C1)
    pendente = _responder(conn, conversa, "Posso ter cachorro?")

    assert pendente == GeracaoPendente(
        pergunta="Posso ter cachorro?", condominio_id=C1, historico=trocas
    )
    assert chamadas == [(conn, CONVERSA_ID, MAX_TROCAS)]


# ── reconfirmação por sessão expirada ────────────────────────────────────────


def test_sessao_velha_reconfirma_o_condominio():
    conn = _FakeConn(nomes={C1: "Alfa"})
    conversa = _conversa(Estado.MENU, condominio=C1, idade_horas=48)
    texto, transicao = _responder(conn, conversa, "1")

    assert "Alfa" in texto
    assert transicao == Transicao.para_confirmacao(C1)


def test_sessao_recente_nao_reconfirma():
    conn = _FakeConn()
    conversa = _conversa(Estado.MENU, condominio=C1, idade_horas=1)
    texto, transicao = _responder(conn, conversa, "1")

    assert transicao == Transicao.para_duvidas(C1)


def test_reconfirmacao_usa_o_id_da_transicao_nao_o_pendente_antigo():
    """Na reconfirmação o pendente da conversa é None; o nome vem do id que a
    transição acabou de mover para condominio_pendente."""
    conn = _FakeConn(nomes={C1: "Alfa"})
    conversa = _conversa(Estado.MENU, condominio=C1, idade_horas=48)
    _responder(conn, conversa, "1")

    assert conn.calls[-1] == ("fetchval", conn.calls[-1][1], (C1,))


# ── mídia ────────────────────────────────────────────────────────────────────


def test_audio_recebe_so_entendo_texto():
    conn = _FakeConn()
    conversa = _conversa(Estado.MENU, condominio=C1)
    texto, transicao = _responder(conn, conversa, None, tipo=MessageType.UNSUPPORTED)

    assert "texto" in texto.lower()
    assert transicao is None
