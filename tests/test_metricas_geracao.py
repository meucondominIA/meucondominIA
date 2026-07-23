"""Testes das métricas puras do eval da geração (Fase 3 · Passo 8)."""

from eval.metricas_geracao import (
    Execucao,
    MetricasResposta,
    avaliar_resposta,
    resumir,
)
from textos import MensagemAtendimento, renderizar

ESPERADAS = ("Regimento, Art. 45", "Regimento, Art. 46")
NAO_SEI_D5 = renderizar(MensagemAtendimento.REGRA_NAO_ENCONTRADA)
CONTINGENCIA = renderizar(MensagemAtendimento.CONTINGENCIA)


def test_citacao_correta_exige_subconjunto_do_gabarito():
    m = avaliar_resposta("Pode.\nFonte: Regimento, Art. 45", ESPERADAS, "com_resposta")
    assert m.citacao_correta is True
    assert m.fontes == ("Regimento, Art. 45",)

    errada = avaliar_resposta("Pode.\nFonte: Outro, Art. 1", ESPERADAS, "com_resposta")
    assert errada.citacao_correta is False

    mista = avaliar_resposta(
        "Pode.\nFonte: Regimento, Art. 45; Outro, Art. 1", ESPERADAS, "com_resposta"
    )
    assert mista.citacao_correta is False


def test_sem_fonte_e_nao_se_aplica_a_sem_resposta():
    m = avaliar_resposta("Não encontrei isso no regimento.", (), "sem_resposta")
    assert m.sem_fonte is True
    assert m.citacao_correta is None


def test_constantes_exatas_identificam_d5_e_contingencia():
    assert avaliar_resposta(NAO_SEI_D5, ESPERADAS, "com_resposta").derrubada_d5
    assert avaliar_resposta(CONTINGENCIA, ESPERADAS, "com_resposta").contingencia
    livre = avaliar_resposta(
        "Não encontrei essa regra. Fale com o síndico.", ESPERADAS, "com_resposta"
    )
    assert not livre.derrubada_d5 and not livre.contingencia


def test_tamanho_contra_o_teto():
    assert avaliar_resposta("x" * 900, (), "sem_resposta").tamanho_ok
    assert not avaliar_resposta("x" * 901, (), "sem_resposta").tamanho_ok


def _exec(
    tipo="com_resposta",
    resposta="Pode.\nFonte: Regimento, Art. 45",
    fiel=True,
    nao_sei=None,
    caso_id="c",
    rep=1,
):
    return Execucao(
        caso_id=caso_id,
        tipo=tipo,
        rep=rep,
        resposta=resposta,
        metricas=avaliar_resposta(
            resposta, ESPERADAS if tipo == "com_resposta" else (), tipo
        ),
        latencia_s=1.0,
        juiz_fiel=fiel,
        juiz_nao_sei=nao_sei,
    )


def test_portoes_todos_passam_no_cenario_limpo():
    execucoes = [_exec(caso_id=f"c{i}") for i in range(10)] + [
        _exec(
            tipo="sem_resposta",
            resposta="Não encontrei essa regra.",
            fiel=None,
            nao_sei=True,
            caso_id="s1",
        )
    ]
    p = resumir(execucoes)
    assert p.citacao_correta_ok and p.nao_sei_ok
    assert p.substantivas_ok and p.tamanho_ok
    assert not p.fidelidade_pendente_revisao


def test_portao_citacao_reprova_abaixo_de_90():
    execucoes = [_exec(caso_id=f"c{i}") for i in range(8)] + [
        _exec(resposta="Pode sim, claro.\nFonte: Outro, Art. 9", caso_id=f"e{i}")
        for i in range(2)
    ]
    p = resumir(execucoes)
    assert p.citacao_correta_pct == 0.8
    assert not p.citacao_correta_ok


def test_nao_sei_reprovado_pelo_juiz_derruba_o_portao():
    execucoes = [
        _exec(
            tipo="sem_resposta",
            resposta="O silêncio é às 22h.",
            fiel=None,
            nao_sei=False,
            caso_id="s1",
        )
    ]
    p = resumir(execucoes)
    assert not p.nao_sei_ok
    assert p.substantivas_sem_citacao == 1 and not p.substantivas_ok
    assert p.fidelidade_pendente_revisao


def test_derrubada_d5_e_informativa_nao_derruba_portao():
    execucoes = [_exec(caso_id="ok"), _exec(resposta=NAO_SEI_D5, fiel=None, caso_id="d5")]
    p = resumir(execucoes)
    assert p.derrubadas_d5 == 1
    assert p.substantivas_ok


def test_metricas_resposta_e_congelada():
    m = avaliar_resposta("x", (), "sem_resposta")
    assert isinstance(m, MetricasResposta)
    assert m.model_config.get("frozen") is True


def test_sentinela_fica_fora_dos_portoes_e_e_contada_a_parte():
    sentinela_falhando = _exec(
        resposta="Não encontrei.", fiel=None, nao_sei=True, caso_id="lim"
    ).model_copy(update={"limitacao": True})
    execucoes = [_exec(caso_id=f"c{i}") for i in range(3)] + [sentinela_falhando]

    p = resumir(execucoes)
    assert p.citacao_correta_pct == 1.0
    assert p.sentinelas == 1 and p.sentinelas_passando == 0
