"""Montagem de contexto: pergunta + trechos + histórico → lista de mensagens
(função pura; nome distinto de mensagens.py, que é o repositório de banco).

Pura como roteador.rotear e textos.renderizar — sem async, sem I/O. Monta a
lista que chat.gerar_resposta consome, na ordem estático→dinâmico que a doc de
prompt caching pede (static no início, variável no fim): [1] developer ESTÁTICO
(system prompt + guardrails + formato da citação), prefixo cacheável e idêntico
para todo condomínio; [2] histórico, até 3 trocas; [3] user com os trechos e a
pergunta atual, no fim.

O system prompt é genérico, SEM nome do condomínio: a identidade já vem nos
trechos (busca Forma B, filtrada por tenant), e um prefixo estático é
compartilhado por todos os tenants em vez de um cache por prédio.

Guardrails (validados contra o modelo real, 22/07/2026): responder só pelos
trechos, admitir quando não cobrem (o LLM decide o não-sei; sem limiar de
distância), não mencionar lei geral, PT-BR curto. A citação (D4) é a fonte
canônica do chunker copiada VERBATIM numa linha `Fonte:` — e NENHUMA linha
`Fonte:` quando não encontrou (sem essa cláusula o modelo despejava todas as
fontes no não-sei). Os colchetes no bloco de trechos são só delimitador de
entrada; o modelo os descarta na saída.

A validação da citação (D5: toda fonte citada ∈ trechos recuperados) NÃO roda
aqui — a montagem é pura e não vê a resposta do modelo. Ela é do Passo 6,
pós-geração; esta função só a HABILITA via o formato verbatim.

Tamanho (~800 chars) é por instrução no prompt: a função pura não emite
parâmetro de API, e o teto rígido (max_output_tokens) tocaria o adapter do
Passo 4. Medido: verbosity=low + a instrução bastam.
"""

from pydantic import BaseModel, ConfigDict

from busca import RegraEncontrada
from chat import MensagemChat, PapelChat

_MAX_TROCAS = 3

_SYSTEM_PROMPT = (
    "Você é o assistente virtual de um condomínio e atende moradores pelo WhatsApp.\n\n"
    "Responda usando EXCLUSIVAMENTE os trechos do regimento fornecidos na mensagem. "
    "Não use conhecimento próprio, não preencha lacunas e não mencione leis gerais "
    "nem legislação externa — apenas o regimento deste condomínio.\n\n"
    "Se os trechos não responderem à pergunta, diga que não encontrou essa regra no "
    "regimento e sugira falar com o síndico. Nunca invente uma resposta. "
    "Nesse caso, NÃO inclua a linha 'Fonte:' — só se cita fonte quando a resposta "
    "vem de um trecho.\n\n"
    "Escreva em português do Brasil, de forma curta e direta (no máximo ~800 caracteres).\n\n"
    "Ao final, cite a(s) fonte(s) do(s) trecho(s) que você usou, copiando EXATAMENTE o "
    "identificador que aparece entre colchetes, no formato:\nFonte: <identificador>\n"
    "Se usar mais de um, separe por '; '."
)


class Troca(BaseModel):
    model_config = ConfigDict(frozen=True)

    pergunta: str
    resposta: str


def montar_mensagens(
    pergunta: str,
    trechos: list[RegraEncontrada],
    historico: list[Troca],
) -> list[MensagemChat]:
    """A lista de mensagens para gerar_resposta: system estático, histórico
    (últimas 3 trocas) e o turno atual com os trechos e a pergunta."""
    mensagens = [MensagemChat(papel=PapelChat.SISTEMA, conteudo=_SYSTEM_PROMPT)]
    for troca in historico[-_MAX_TROCAS:]:
        mensagens.append(MensagemChat(papel=PapelChat.USUARIO, conteudo=troca.pergunta))
        mensagens.append(
            MensagemChat(papel=PapelChat.ASSISTENTE, conteudo=troca.resposta)
        )
    mensagens.append(
        MensagemChat(
            papel=PapelChat.USUARIO,
            conteudo=f"{_bloco_trechos(trechos)}\nPergunta: {pergunta}",
        )
    )
    return mensagens


def _bloco_trechos(trechos: list[RegraEncontrada]) -> str:
    if not trechos:
        return "Nenhum trecho do regimento foi encontrado para esta pergunta."
    blocos = ["Trechos do regimento do condomínio:\n"]
    for trecho in trechos:
        blocos.append(f"[{trecho.fonte}]\n{trecho.conteudo}\n")
    return "\n".join(blocos)
