"""Serviço de geração: pergunta → resposta fiel ao regimento (Fase 3 · Passo 6).

O espelho de busca.py: costura buscar_trechos (Fase 2) + montar_mensagens
(Passo 5) + gerar_resposta (Passo 4) e devolve texto pronto para envio — SEMPRE
texto, nunca exceção de rede (política B9): falha das duas redes vira a
contingência do textos.py. QUALQUER outra exceção sobe, porque bug não é rede;
o _gerar do processador segue como última rede das duas camadas.

A regra de ouro vale por construção: este módulo não adquire conexão nenhuma —
nem importa db. O histórico chega pronto do chamador (Passo 7, que o lê na
janela de conexão que já tem), e a conexão do SELECT de similaridade vive
dentro de busca.py, curta, depois do embedding. Pior caso das duas redes em
sequência ≈ 19s (medido); nenhuma conexão do pool atravessa isso.

As duas pernas de rede falham com exceções NOSSAS: os dois adapters embrulham
o SDK (anti-corrupção também no erro), então este módulo não conhece openai.

Validação de citação (D5): toda fonte citada tem que existir entre os trechos
recuperados — match EXATO, o modelo ecoa verbatim (medido, 22/07/2026). Sem
linha 'Fonte:' não há o que validar: é o caminho legítimo do não-sei. Citação
desconhecida derruba a resposta INTEIRA para REGRA_NAO_ENCONTRADA, com warning
no log — o dado que o eval do Passo 8 quantifica. O check não detecta resposta
substantiva sem citação: isso é métrica do eval, não guarda de runtime.
"""

import logging
from uuid import UUID

from busca import buscar_trechos
from chat import ChatIndisponivelError, ChatRespostaError, gerar_resposta
from contexto import Troca, montar_mensagens
from embeddings import EmbeddingIndisponivelError, EmbeddingRespostaError
from textos import MensagemAtendimento, renderizar

logger = logging.getLogger(__name__)

_PREFIXO_FONTE = "Fonte:"


async def responder_duvida(
    pergunta: str,
    condominio_id: UUID,
    historico: list[Troca],
) -> str:
    """A resposta à dúvida do morador, pronta para envio."""
    try:
        trechos = await buscar_trechos(pergunta, condominio_id)
        resposta = await gerar_resposta(montar_mensagens(pergunta, trechos, historico))
    except (
        EmbeddingIndisponivelError,
        EmbeddingRespostaError,
        ChatIndisponivelError,
        ChatRespostaError,
    ):
        logger.exception("geração falhou: condominio_id=%s", condominio_id)
        return renderizar(MensagemAtendimento.CONTINGENCIA)

    conhecidas = {trecho.fonte for trecho in trechos}
    desconhecidas = [f for f in _fontes_citadas(resposta) if f not in conhecidas]
    if desconhecidas:
        logger.warning(
            "citação não confere: condominio_id=%s desconhecidas=%s",
            condominio_id,
            desconhecidas,
        )
        return renderizar(MensagemAtendimento.REGRA_NAO_ENCONTRADA)
    return resposta


def _fontes_citadas(resposta: str) -> list[str]:
    """As fontes das linhas 'Fonte:', divididas por ';' e sem colchetes
    (variante real do modelo, observada antes da cláusula do não-sei)."""
    citadas = []
    for linha in resposta.splitlines():
        linha = linha.strip()
        if not linha.startswith(_PREFIXO_FONTE):
            continue
        for item in linha.removeprefix(_PREFIXO_FONTE).split(";"):
            item = item.strip().strip("[]").strip()
            if item:
                citadas.append(item)
    return citadas
