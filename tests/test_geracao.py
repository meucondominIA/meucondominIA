"""Testes do serviço de geração (Fase 3 · Passo 6).

Fakes SÓ nas duas redes (buscar_trechos e gerar_resposta, via monkeypatch no
namespace de geracao) — montar_mensagens é pura e roda DE VERDADE na costura.
Zero token: a costura se prova sem rede, como a missão do passo pede.
"""

import asyncio
from uuid import uuid4

import pytest

import geracao
from busca import RegraEncontrada
from chat import ChatIndisponivelError, ChatRespostaError, PapelChat
from contexto import Troca
from embeddings import EmbeddingIndisponivelError, EmbeddingRespostaError
from textos import MensagemAtendimento, renderizar

_ID = uuid4()

_TRECHOS = [
    RegraEncontrada(
        conteudo="Art. 15º - É permitida a permanência de animais de pequeno porte.",
        fonte="Regimento Gabro, Art. 15º",
        distancia=0.2,
    ),
    RegraEncontrada(
        conteudo="Art. 22 - A piscina funciona das 8h às 22h.",
        fonte="Regimento Gabro, Art. 22",
        distancia=0.4,
    ),
]

_CONTINGENCIA = renderizar(MensagemAtendimento.CONTINGENCIA)
_NAO_ENCONTRADA = renderizar(MensagemAtendimento.REGRA_NAO_ENCONTRADA)


def _prepara(monkeypatch, *, trechos=_TRECHOS, resposta="", erro_busca=None, erro_chat=None):
    async def fake_buscar(pergunta, condominio_id):
        if erro_busca is not None:
            raise erro_busca
        return trechos

    async def fake_chat(mensagens):
        if erro_chat is not None:
            raise erro_chat
        return resposta

    monkeypatch.setattr(geracao, "buscar_trechos", fake_buscar)
    monkeypatch.setattr(geracao, "gerar_resposta", fake_chat)


def _responder(pergunta="posso ter cachorro?", historico=()):
    return asyncio.run(geracao.responder_duvida(pergunta, _ID, list(historico)))


def test_happy_path_costura_na_ordem_e_devolve_verbatim(monkeypatch):
    chamadas: list[str] = []
    recebidas = {}
    texto = "Pode sim, de pequeno porte.\nFonte: Regimento Gabro, Art. 15º"

    async def fake_buscar(pergunta, condominio_id):
        chamadas.append("buscar")
        return _TRECHOS

    async def fake_chat(mensagens):
        chamadas.append("chat")
        recebidas["mensagens"] = mensagens
        return texto

    monkeypatch.setattr(geracao, "buscar_trechos", fake_buscar)
    monkeypatch.setattr(geracao, "gerar_resposta", fake_chat)

    resultado = _responder(historico=[Troca(pergunta="oi?", resposta="olá!")])

    assert resultado == texto
    assert chamadas == ["buscar", "chat"]
    msgs = recebidas["mensagens"]
    assert msgs[0].papel is PapelChat.SISTEMA
    assert msgs[1].conteudo == "oi?"
    assert "Pergunta: posso ter cachorro?" in msgs[-1].conteudo
    assert "[Regimento Gabro, Art. 15º]" in msgs[-1].conteudo


def test_citacao_fabricada_vira_regra_nao_encontrada(monkeypatch):
    _prepara(monkeypatch, resposta="Pode sim.\nFonte: Regimento Gabro, Art. 99")
    assert _responder() == _NAO_ENCONTRADA


def test_citacao_mista_tambem_derruba(monkeypatch):
    _prepara(
        monkeypatch,
        resposta="Pode.\nFonte: Regimento Gabro, Art. 15º; Regimento Gabro, Art. 99",
    )
    assert _responder() == _NAO_ENCONTRADA


def test_sem_linha_fonte_passa_direto(monkeypatch):
    texto = "Não encontrei essa regra no regimento. Sugiro falar com o síndico."
    _prepara(monkeypatch, resposta=texto)
    assert _responder() == texto


def test_variante_com_colchetes_confere(monkeypatch):
    texto = "Pode sim.\nFonte: [Regimento Gabro, Art. 15º]"
    _prepara(monkeypatch, resposta=texto)
    assert _responder() == texto


def test_trechos_vazios_com_citacao_derruba(monkeypatch):
    _prepara(monkeypatch, trechos=[], resposta="Pode.\nFonte: Regimento Gabro, Art. 15º")
    assert _responder() == _NAO_ENCONTRADA


def test_embedding_indisponivel_vira_contingencia(monkeypatch):
    _prepara(
        monkeypatch, erro_busca=EmbeddingIndisponivelError("embeddings indisponíveis")
    )
    assert _responder() == _CONTINGENCIA


def test_embedding_resposta_error_vira_contingencia(monkeypatch):
    _prepara(monkeypatch, erro_busca=EmbeddingRespostaError("contagem"))
    assert _responder() == _CONTINGENCIA


def test_chat_indisponivel_vira_contingencia(monkeypatch):
    _prepara(monkeypatch, erro_chat=ChatIndisponivelError("chat indisponível"))
    assert _responder() == _CONTINGENCIA


def test_chat_resposta_error_vira_contingencia(monkeypatch):
    _prepara(monkeypatch, erro_chat=ChatRespostaError("status 'incomplete'"))
    assert _responder() == _CONTINGENCIA


def test_excecao_inesperada_sobe(monkeypatch):
    _prepara(monkeypatch, erro_busca=RuntimeError("bug, não rede"))
    with pytest.raises(RuntimeError):
        _responder()


def test_fontes_citadas_cobre_as_variantes_reais():
    assert geracao._fontes_citadas("texto sem fonte") == []
    assert geracao._fontes_citadas("ok\nFonte: A") == ["A"]
    assert geracao._fontes_citadas("ok\nFonte: A; B") == ["A", "B"]
    assert geracao._fontes_citadas("ok\nFonte: [A]; [B]") == ["A", "B"]
    assert geracao._fontes_citadas("Fonte: A\nmeio\nFonte: B") == ["A", "B"]
    assert geracao._fontes_citadas("Fonte:") == []
    assert geracao._fontes_citadas("  Fonte:   A  ") == ["A"]
