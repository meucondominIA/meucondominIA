"""Juiz LLM da avaliação da geração (Fase 3 · Passo 8) — só do eval.

Modelo DIFERENTE e mais forte que o gerador (prática oficial de eval), por isso
fala com o SDK diretamente: o adapter de produção fixa o modelo dos Settings e
não serve dois. Nunca importado pela produção. store=False como em todo o
projeto.

Rubricas binárias no molde cot_classify do openai/evals (closedqa): o juiz
raciocina passo a passo PRIMEIRO e o veredito é a ÚLTIMA linha, sozinho —
julgamento depois do raciocínio, nunca antes.
"""

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict

from busca import RegraEncontrada

MODELO_PADRAO = "gpt-5.6-terra"
_TIMEOUT = 60.0
_RETRIES = 2

_FORMATO = (
    "Raciocine passo a passo sobre o critério antes de concluir.\n"
    "Na penúltima linha, escreva 'Justificativa: ' e uma frase objetiva.\n"
    "Na última linha, escreva APENAS a palavra APROVADO ou REPROVADO."
)

_RUBRICA_FIDELIDADE = (
    "Você audita as respostas de um assistente de condomínio.\n"
    "Critério: TODA afirmação factual da RESPOSTA (regras, valores, horários, "
    "permissões, proibições, procedimentos) está diretamente suportada pelos "
    "TRECHOS do regimento fornecidos.\n"
    "Saudações, estilo, a linha 'Fonte:' e a sugestão de falar com o síndico "
    "não violam o critério. Admitir que não encontrou a regra não viola.\n"
    "REPROVE se a resposta afirmar qualquer coisa que os trechos não "
    f"sustentam.\n\n{_FORMATO}"
)

_RUBRICA_NAO_SEI = (
    "Você audita as respostas de um assistente de condomínio.\n"
    "Critério: a RESPOSTA admite claramente que a regra perguntada não foi "
    "encontrada no regimento. Ela PODE agregar contexto relacionado CURTO "
    "(no máximo duas frases) desde que verdadeiro conforme os TRECHOS e sem "
    "apresentá-lo como resposta à pergunta.\n"
    "Saudações, estilo e a sugestão de falar com o síndico não reprovam.\n"
    "REPROVE se ela afirmar uma regra como se respondesse à pergunta, se "
    "inventar informação que não está nos trechos, ou se o contexto extra "
    f"passar de duas frases.\n\n{_FORMATO}"
)


class VereditoJuiz(BaseModel):
    model_config = ConfigDict(frozen=True)

    aprovado: bool
    justificativa: str


class JuizError(Exception):
    """O juiz não devolveu um veredito utilizável."""


async def julgar_fidelidade(
    cliente: AsyncOpenAI,
    modelo: str,
    trechos: list[RegraEncontrada],
    resposta: str,
) -> VereditoJuiz:
    blocos = "\n\n".join(f"[{t.fonte}]\n{t.conteudo}" for t in trechos)
    conteudo = f"TRECHOS:\n{blocos}\n\nRESPOSTA:\n{resposta}"
    return await _julgar(cliente, modelo, _RUBRICA_FIDELIDADE, conteudo)


async def julgar_nao_sei(
    cliente: AsyncOpenAI,
    modelo: str,
    trechos: list[RegraEncontrada],
    resposta: str,
) -> VereditoJuiz:
    blocos = "\n\n".join(f"[{t.fonte}]\n{t.conteudo}" for t in trechos)
    conteudo = f"TRECHOS:\n{blocos}\n\nRESPOSTA:\n{resposta}"
    return await _julgar(cliente, modelo, _RUBRICA_NAO_SEI, conteudo)


async def _julgar(
    cliente: AsyncOpenAI, modelo: str, rubrica: str, conteudo: str
) -> VereditoJuiz:
    resposta = await cliente.with_options(
        timeout=_TIMEOUT, max_retries=_RETRIES
    ).responses.create(
        model=modelo,
        input=[
            {"role": "developer", "content": rubrica},
            {"role": "user", "content": conteudo},
        ],
        store=False,
    )
    if resposta.status != "completed":
        raise JuizError(f"status {resposta.status!r} (não 'completed')")
    return _parse(resposta.output_text)


def _parse(texto: str) -> VereditoJuiz:
    linhas = [linha.strip() for linha in texto.strip().splitlines() if linha.strip()]
    if not linhas:
        raise JuizError("veredito vazio")
    veredito = linhas[-1].upper().strip(".!")
    justificativa = next(
        (
            linha.removeprefix("Justificativa:").strip()
            for linha in linhas[:-1]
            if linha.startswith("Justificativa:")
        ),
        " ".join(linhas[:-1])[:500],
    )
    if veredito == "REPROVADO":
        return VereditoJuiz(aprovado=False, justificativa=justificativa)
    if veredito == "APROVADO":
        return VereditoJuiz(aprovado=True, justificativa=justificativa)
    raise JuizError(f"veredito não reconhecido: {linhas[-1]!r}")
