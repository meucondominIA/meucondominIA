"""Testes do juiz do eval (Fase 3 · Passo 8) — SDK dublado, sem rede."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from eval.juiz import (
    JuizError,
    _parse,
    julgar_fidelidade,
    julgar_nao_sei,
)
from regras import RegraEncontrada

TRECHOS = [RegraEncontrada(conteudo="Art. 45 ...", fonte="Regimento, Art. 45", distancia=0.3)]


def _cliente(output_text="Raciocínio em uma linha.\nAPROVADO", status="completed"):
    create = AsyncMock(
        return_value=SimpleNamespace(status=status, output_text=output_text)
    )
    inner = SimpleNamespace(responses=SimpleNamespace(create=create))
    return SimpleNamespace(with_options=lambda **kw: inner), create


def test_parse_veredito_na_ultima_linha():
    v = _parse("Passo 1: confere.\nPasso 2: tudo suportado.\nAPROVADO")
    assert v.aprovado is True
    assert "confere" in v.justificativa

    v = _parse("A resposta inventa horário.\nREPROVADO.")
    assert v.aprovado is False


def test_parse_rejeita_veredito_ausente_ou_estranho():
    with pytest.raises(JuizError):
        _parse("")
    with pytest.raises(JuizError):
        _parse("Analisando...\nTALVEZ")


def test_julgar_fidelidade_monta_trechos_e_resposta():
    cliente, create = _cliente()
    v = asyncio.run(julgar_fidelidade(cliente, "juiz-x", TRECHOS, "Pode.\nFonte: F"))
    assert v.aprovado is True

    kwargs = create.await_args.kwargs
    assert kwargs["model"] == "juiz-x"
    assert kwargs["store"] is False
    developer, user = kwargs["input"]
    assert "passo a passo" in developer["content"]
    assert "[Regimento, Art. 45]" in user["content"]
    assert "Pode.\nFonte: F" in user["content"]


def test_julgar_nao_sei_reprova():
    cliente, create = _cliente(output_text="Afirma regra de horário.\nREPROVADO")
    v = asyncio.run(
        julgar_nao_sei(cliente, "juiz-x", TRECHOS, "O silêncio é às 22h.")
    )
    assert v.aprovado is False
    developer, user = create.await_args.kwargs["input"]
    assert "duas frases" in developer["content"]
    assert "síndico não reprovam" in developer["content"]
    assert "[Regimento, Art. 45]" in user["content"]


def test_status_nao_completed_levanta():
    cliente, _ = _cliente(status="incomplete")
    with pytest.raises(JuizError):
        asyncio.run(julgar_nao_sei(cliente, "juiz-x", TRECHOS, "x"))
