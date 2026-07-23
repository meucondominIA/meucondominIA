"""Métricas puras da avaliação da geração (Fase 3 · Passo 8).

Grading de código: o que é decidível sem julgamento. O parser de citação é o
MESMO da produção (geracao.fontes_citadas) — o eval mede o que o morador vê.
O não-sei do modelo é texto livre; a igualdade EXATA com as constantes do
textos.py identifica derrubada D5 e contingência (únicas origens delas).

A parte que exige julgamento (fidelidade, não-sei honesto) chega pronta do
juiz e entra aqui só na agregação dos portões.
"""

from pydantic import BaseModel, ConfigDict

from geracao import fontes_citadas
from textos import MensagemAtendimento, renderizar

TAMANHO_MAX = 900

_NAO_SEI_D5 = renderizar(MensagemAtendimento.REGRA_NAO_ENCONTRADA)
_CONTINGENCIA = renderizar(MensagemAtendimento.CONTINGENCIA)


class MetricasResposta(BaseModel):
    model_config = ConfigDict(frozen=True)

    fontes: tuple[str, ...]
    citacao_correta: bool | None
    sem_fonte: bool
    derrubada_d5: bool
    contingencia: bool
    tamanho: int
    tamanho_ok: bool


def avaliar_resposta(
    resposta: str, esperadas: tuple[str, ...], tipo: str
) -> MetricasResposta:
    """O que o código decide sozinho sobre uma resposta.

    citacao_correta só existe para com_resposta: citou algo E tudo que citou
    está no gabarito. None = não se aplica (sem_resposta).
    """
    fontes = tuple(fontes_citadas(resposta))
    citacao_correta = None
    if tipo == "com_resposta":
        citacao_correta = bool(fontes) and all(f in esperadas for f in fontes)
    return MetricasResposta(
        fontes=fontes,
        citacao_correta=citacao_correta,
        sem_fonte=not fontes,
        derrubada_d5=resposta == _NAO_SEI_D5,
        contingencia=resposta == _CONTINGENCIA,
        tamanho=len(resposta),
        tamanho_ok=len(resposta) <= TAMANHO_MAX,
    )


class Execucao(BaseModel):
    """Uma resposta avaliada: métricas de código + vereditos do juiz."""

    model_config = ConfigDict(frozen=True)

    caso_id: str
    tipo: str
    rep: int
    resposta: str
    metricas: MetricasResposta
    latencia_s: float
    limitacao: bool = False
    juiz_fiel: bool | None = None
    juiz_nao_sei: bool | None = None
    juiz_justificativa: str = ""


class Portoes(BaseModel):
    model_config = ConfigDict(frozen=True)

    citacao_correta_pct: float
    citacao_correta_ok: bool
    nao_sei_pct: float
    nao_sei_ok: bool
    substantivas_sem_citacao: int
    substantivas_ok: bool
    tamanho_pct: float
    tamanho_ok: bool
    reprovadas_pelo_juiz: int
    fidelidade_pendente_revisao: bool
    derrubadas_d5: int
    contingencias: int
    sentinelas: int
    sentinelas_passando: int


def resumir(execucoes: list[Execucao]) -> Portoes:
    """Os portões aprovados em 23/07/2026, agregados sobre EXECUÇÕES (não casos):
    citação ≥90% · não-sei 100% · substantiva-sem-citação 0 · tamanho ≥95% ·
    fidelidade = zero invenção CONFIRMADA (reprovação do juiz vai à revisão
    humana antes de contar). D5/contingência: informativos.

    Sentinelas (limitação conhecida, decisão de 23/07): fora dos portões,
    contadas à parte — quando uma fase futura resolver a causa, elas acusam.
    """
    sentinelas = [e for e in execucoes if e.limitacao]
    passando = [
        e
        for e in sentinelas
        if e.metricas.citacao_correta and e.juiz_fiel is not False
    ]
    execucoes = [e for e in execucoes if not e.limitacao]
    com = [e for e in execucoes if e.tipo == "com_resposta"]
    sem = [e for e in execucoes if e.tipo == "sem_resposta"]

    citacoes = [e for e in com if e.metricas.citacao_correta]
    nao_seis = [e for e in sem if e.juiz_nao_sei is not False]
    substantivas = [
        e
        for e in execucoes
        if e.metricas.sem_fonte
        and not e.metricas.derrubada_d5
        and not e.metricas.contingencia
        and e.juiz_nao_sei is False
    ]
    tamanhos_ok = [e for e in execucoes if e.metricas.tamanho_ok]
    reprovadas = [
        e for e in execucoes if e.juiz_fiel is False or e.juiz_nao_sei is False
    ]

    citacao_pct = len(citacoes) / len(com) if com else 1.0
    nao_sei_pct = len(nao_seis) / len(sem) if sem else 1.0
    tamanho_pct = len(tamanhos_ok) / len(execucoes) if execucoes else 1.0

    return Portoes(
        citacao_correta_pct=citacao_pct,
        citacao_correta_ok=citacao_pct >= 0.90,
        nao_sei_pct=nao_sei_pct,
        nao_sei_ok=nao_sei_pct >= 1.0,
        substantivas_sem_citacao=len(substantivas),
        substantivas_ok=not substantivas,
        tamanho_pct=tamanho_pct,
        tamanho_ok=tamanho_pct >= 0.95,
        reprovadas_pelo_juiz=len(reprovadas),
        fidelidade_pendente_revisao=bool(reprovadas),
        derrubadas_d5=sum(e.metricas.derrubada_d5 for e in execucoes),
        contingencias=sum(e.metricas.contingencia for e in execucoes),
        sentinelas=len(sentinelas),
        sentinelas_passando=len(passando),
    )
