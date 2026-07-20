"""Testes das métricas puras do eval (Fase 2 · Passo 7). Sem I/O, sem rede."""

import pytest

from eval.metricas import avaliar_caso, resumir

TENANT = frozenset({"Doc, Art. 1º", "Doc, Art. 2º", "Doc, Art. 3º", "Doc, Art. 4º"})


def _caso(esperadas, obtidas, distancias=None):
    if distancias is None:
        distancias = tuple(0.3 + i * 0.1 for i in range(len(obtidas)))
    return avaliar_caso("pergunta?", esperadas, obtidas, distancias, TENANT)


def test_hit_1_exige_a_esperada_no_topo():
    assert _caso(("Doc, Art. 1º",), ("Doc, Art. 1º", "Doc, Art. 2º")).hit_1
    assert not _caso(("Doc, Art. 1º",), ("Doc, Art. 2º", "Doc, Art. 1º")).hit_1


def test_hit_k_aceita_em_qualquer_posicao():
    caso = _caso(("Doc, Art. 1º",), ("Doc, Art. 2º", "Doc, Art. 1º"))
    assert caso.hit_k and not caso.hit_1


def test_completude_exige_todas_as_esperadas():
    esperadas = ("Doc, Art. 1º", "Doc, Art. 3º")
    completo = _caso(esperadas, ("Doc, Art. 1º", "Doc, Art. 2º", "Doc, Art. 3º"))
    parcial = _caso(esperadas, ("Doc, Art. 1º", "Doc, Art. 2º"))
    assert completo.completude
    assert parcial.hit_k and not parcial.completude


def test_sem_gabarito_nao_pontua_nada():
    caso = _caso((), ("Doc, Art. 1º",))
    assert caso.sem_gabarito
    assert not caso.hit_1 and not caso.hit_k


def test_vazamento_e_fonte_fora_do_tenant():
    caso = _caso(("Doc, Art. 1º",), ("Doc, Art. 1º", "Sentinela, Art. 2º"))
    assert caso.vazamentos == ("Sentinela, Art. 2º",)


def test_obtidas_e_distancias_desalinhadas_falham():
    with pytest.raises(ValueError, match="desalinhadas"):
        avaliar_caso("p?", (), ("Doc, Art. 1º",), (0.1, 0.2), TENANT)


def test_resumo_agrega_contagens_e_distancias():
    resultados = [
        _caso(("Doc, Art. 1º",), ("Doc, Art. 1º",), (0.30,)),
        _caso(
            ("Doc, Art. 1º", "Doc, Art. 3º"),
            ("Doc, Art. 2º", "Doc, Art. 1º"),
            (0.40, 0.45),
        ),
        _caso(("Doc, Art. 3º",), ("Doc, Art. 4º",), (0.70,)),
        _caso((), ("Doc, Art. 2º",), (0.65,)),
    ]
    resumo = resumir(resultados)
    assert resumo.casos == 4
    assert resumo.com_gabarito == 3
    assert resumo.hit_1 == 1
    assert resumo.hit_k == 2
    assert resumo.completude == 1
    assert resumo.vazamentos == 0
    assert resumo.dist_acertos == (0.30, 0.45)
    assert resumo.dist_top1_erros == (0.40, 0.70)
    assert resumo.dist_sem_gabarito == (0.65,)


def test_resumo_conta_vazamentos_incluindo_casos_sem_gabarito():
    resultados = [_caso((), ("Intruso, Art. 9º",), (0.2,))]
    assert resumir(resultados).vazamentos == 1
