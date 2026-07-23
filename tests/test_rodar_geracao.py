"""Testes da borda do harness de geração (Fase 3 · Passo 8) — parse do golden."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from eval.rodar_geracao import CasoGeracao, GoldenGeracao


def _caso(**kw):
    base = {
        "id": "c1",
        "tipo": "com_resposta",
        "pergunta": "Pode?",
        "esperadas": ["Regimento, Art. 1"],
    }
    base.update(kw)
    return base


def test_golden_real_do_repo_e_valido():
    bruto = json.loads(
        Path("eval/golden_geracao.json").read_text(encoding="utf-8")
    )
    golden = GoldenGeracao.model_validate(bruto)
    assert len(golden.casos) == 31
    assert sum(c.tipo == "sem_resposta" for c in golden.casos) == 5
    assert any(c.historico for c in golden.casos)


def test_com_resposta_sem_esperadas_e_erro():
    with pytest.raises(ValidationError, match="exige esperadas"):
        CasoGeracao.model_validate(_caso(esperadas=[]))


def test_sem_resposta_com_esperadas_e_erro():
    with pytest.raises(ValidationError, match="não leva esperadas"):
        CasoGeracao.model_validate(_caso(tipo="sem_resposta"))


def test_chave_desconhecida_e_erro_nao_descarte():
    with pytest.raises(ValidationError):
        CasoGeracao.model_validate(_caso(esperada="typo"))


def test_ids_repetidos_derrubam_o_golden():
    with pytest.raises(ValidationError, match="ids repetidos"):
        GoldenGeracao.model_validate(
            {"condominio": "x", "casos": [_caso(), _caso()]}
        )
