"""Máquina de estados do atendimento (Fase 3 · Passo 2).

Ponto ÚNICO de decisão: dado o estado da conversa e a mensagem, decide o que
fazer. Decide, não executa — nada aqui toca banco, rede ou relógio.

`def` e não `async def` de propósito: função síncrona não consegue `await`, logo
não consegue tocar I/O nem por acidente nem daqui a seis meses. A pureza vira
propriedade da linguagem em vez de combinado, como o CHECK faz no banco.

Tudo é número. A confirmação é 1/2 como o menu é 1..5, e o escape é 0 — que fica
estruturalmente fora da faixa das listas (que começam em 1), então nunca colide
por mais que o menu cresça. Some daqui qualquer comparação de palavra: não há
'sim', 'não' nem sinônimo a adivinhar, e por isso não há acento a normalizar.

Uma decisão = UMA mensagem. Não é preferência: uq_mensagens_em_resposta_a é
único, então o banco só aceita uma resposta por mensagem recebida.

A decisão nomeia a mensagem (Mensagem.MENU), não escreve o texto — a redação é
do passo 3. E duas decisões DELEGAM, porque não podem ser resolvidas sem I/O:
qual condomínio é o item N exige a lista (passo 3), e responder dúvida exige
RAG + LLM (passo 6).

Transicao só nasce pelos construtores nomeados: a trinca ilegal fica
irrepresentável aqui, do mesmo jeito que chk_conversas_estado_coerente a torna
ingravável no banco. O CHECK continua sendo a garantia — isto é conveniência
para quem chama, não segunda validação.
"""

import re
from enum import Enum, IntEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from zpro_models import MessageType


class Estado(str, Enum):
    """Espelha chk_conversas_estado. Mudar aqui exige migration."""

    IDENTIFICACAO = "identificacao"
    AGUARDANDO_CONFIRMACAO = "aguardando_confirmacao"
    MENU = "menu"
    DUVIDAS = "duvidas"


class Mensagem(str, Enum):
    """Identidade da mensagem; a redação é do passo 3."""

    PEDIR_CONDOMINIO = "pedir_condominio"
    CONDOMINIO_NAO_ENTENDIDO = "condominio_nao_entendido"
    CONFIRMACAO_NAO_ENTENDIDA = "confirmacao_nao_entendida"
    MENU = "menu"
    MENU_NAO_ENTENDIDO = "menu_nao_entendido"
    OPCAO_INDISPONIVEL = "opcao_indisponivel"
    CONVITE_PERGUNTA = "convite_pergunta"
    PERGUNTA_VAZIA = "pergunta_vazia"
    SO_ENTENDO_TEXTO = "so_entendo_texto"


ESCAPE = 0


class OpcaoConfirmacao(IntEnum):
    SIM = 1
    NAO = 2


class OpcaoMenu(IntEnum):
    """Destinos do menu. Os rótulos são do passo 3; o roteamento é daqui."""

    DUVIDAS = 1
    SINDICO = 2
    RESERVA = 3
    OCORRENCIA = 4
    TROCAR_CONDOMINIO = 5


_INDISPONIVEIS = frozenset({OpcaoMenu.SINDICO, OpcaoMenu.RESERVA, OpcaoMenu.OCORRENCIA})

# Tolera o que as pessoas digitam de verdade: espaço em volta, "1." e "1)"
# copiados do formato da lista, zero à esquerda. Por extenso não entra — abriria
# a porta que a escolha por número fechou.
_OPCAO = re.compile(r"\s*(\d{1,2})[.)]?\s*")


class Transicao(BaseModel):
    """A trinca completa de destino — o CHECK do banco é sobre as três colunas."""

    model_config = ConfigDict(frozen=True)

    estado: Estado
    condominio_id: UUID | None
    condominio_pendente: UUID | None

    @classmethod
    def para_identificacao(cls) -> Self:
        return cls(
            estado=Estado.IDENTIFICACAO, condominio_id=None, condominio_pendente=None
        )

    @classmethod
    def para_confirmacao(cls, candidato: UUID) -> Self:
        return cls(
            estado=Estado.AGUARDANDO_CONFIRMACAO,
            condominio_id=None,
            condominio_pendente=candidato,
        )

    @classmethod
    def para_menu(cls, condominio: UUID) -> Self:
        return cls(
            estado=Estado.MENU, condominio_id=condominio, condominio_pendente=None
        )

    @classmethod
    def para_duvidas(cls, condominio: UUID) -> Self:
        return cls(
            estado=Estado.DUVIDAS, condominio_id=condominio, condominio_pendente=None
        )


class Conversa(BaseModel):
    """O que o roteador precisa saber da conversa — espelha as colunas."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    estado: Estado
    condominio_id: UUID | None
    condominio_pendente: UUID | None


class Responder(BaseModel):
    """Resolvida sem I/O: manda a mensagem, e talvez mude o estado."""

    model_config = ConfigDict(frozen=True)

    mensagem: Mensagem
    transicao: Transicao | None = None


class DelegarIdentificacao(BaseModel):
    """O morador escolheu o item N da lista; quem resolve é o passo 3."""

    model_config = ConfigDict(frozen=True)

    indice: int


class DelegarDuvida(BaseModel):
    """O morador perguntou; quem responde é o serviço de geração (passo 6)."""

    model_config = ConfigDict(frozen=True)

    pergunta: str


Decisao = Responder | DelegarIdentificacao | DelegarDuvida


def _opcao(texto: str) -> int | None:
    """Lê a escolha do morador: "2", " 2 " e "2." viram 2; "quero falar com o
    síndico" vira None.

    Existe porque o que chega é sempre texto livre, e todo estado precisa fazer
    a mesma pergunta antes de decidir: isto é a escolha de um item, ou é outra
    coisa?
    """
    achado = _OPCAO.fullmatch(texto)
    return int(achado.group(1)) if achado else None


def _recomecar_identificacao() -> Responder:
    """Manda de volta para a lista de condomínios, zerando os dois tenants.

    Três situações reais chegam aqui: o morador escolheu "não sou desse
    condomínio" no menu (mudou de prédio, ou errou na primeira vez); respondeu
    "não" na confirmação; ou o condomínio candidato foi apagado do banco e a
    conversa ficou sem para onde ir.

    Zerar os dois é exigência do CHECK — e é o que impede responder com o
    condomínio antigo enquanto a pessoa ainda não confirmou o novo.
    """
    return Responder(
        mensagem=Mensagem.PEDIR_CONDOMINIO, transicao=Transicao.para_identificacao()
    )


def rotear(conversa: Conversa, *, tipo: MessageType, texto: str | None) -> Decisao:
    """Porta única: toda mensagem do morador entra aqui e sai como decisão.

    Duas guardas antes de olhar o estado, e a ORDEM entre elas importa. Quem
    caiu em aguardando_confirmacao sem candidato precisa sair de lá mesmo
    mandando áudio — se o filtro de tipo viesse primeiro, a pessoa ouviria "só
    entendo texto" para sempre, sem nunca voltar à lista.
    """
    if (
        conversa.estado is Estado.AGUARDANDO_CONFIRMACAO
        and conversa.condominio_pendente is None
    ):
        return _recomecar_identificacao()

    if tipo is not MessageType.TEXT or texto is None:
        return Responder(mensagem=Mensagem.SO_ENTENDO_TEXTO)

    match conversa.estado:
        case Estado.IDENTIFICACAO:
            return _identificacao(texto)
        case Estado.AGUARDANDO_CONFIRMACAO:
            return _confirmacao(conversa, texto)
        case Estado.MENU:
            return _menu(conversa, texto)
        case Estado.DUVIDAS:
            return _duvidas(conversa, texto)


def _identificacao(texto: str) -> Decisao:
    """Primeiro contato: o sistema mandou a lista de condomínios e espera o número.

    "2" vira delegação — o roteador NÃO sabe se existe um segundo condomínio,
    porque isso exige ler o banco. Quem confere é o passo 3.

    "moro no Gabro", "   " e "0" caem todos no mesmo lugar: pedir o número de
    novo. O 0 é escape nos outros estados, mas aqui não existe menu para voltar,
    e prometer um atalho que não leva a lugar nenhum é pior do que ignorá-lo.
    """
    opcao = _opcao(texto)
    if opcao is None or opcao < 1:
        return Responder(mensagem=Mensagem.CONDOMINIO_NAO_ENTENDIDO)
    return DelegarIdentificacao(indice=opcao)


def _confirmacao(conversa: Conversa, texto: str) -> Decisao:
    """O sistema perguntou "é o Edifício Gabro? 1 - Sim, 2 - Não" e espera resposta.

    O 1 é o ÚNICO ponto do sistema onde um condomínio entra em condominio_id, e
    é por isso que a confirmação existe: a conversa não expira, então um erro
    aqui se fixa em silêncio e o morador passa a receber as regras do prédio
    errado.

    O 2 volta à lista. Qualquer outra coisa ("sim", "3", "isso mesmo")
    repergunta — depois que a escolha virou número, palavra não é resposta.
    """
    candidato = conversa.condominio_pendente
    if candidato is None:
        return _recomecar_identificacao()

    match _opcao(texto):
        case OpcaoConfirmacao.SIM:
            return Responder(
                mensagem=Mensagem.MENU, transicao=Transicao.para_menu(candidato)
            )
        case OpcaoConfirmacao.NAO:
            return _recomecar_identificacao()
        case _:
            return Responder(mensagem=Mensagem.CONFIRMACAO_NAO_ENTENDIDA)


def _menu(conversa: Conversa, texto: str) -> Decisao:
    """Morador já identificado, escolhendo o que quer fazer.

    O 1 abre o fluxo de dúvidas — hoje a única opção implementada, e a única que
    chama IA. 2, 3 e 4 respondem "indisponível" sem prometer prazo. O 5 é a
    saída de quem não é (ou deixou de ser) desse condomínio.

    "quero falar com o síndico" digitado por extenso NÃO vira erro: remostra o
    menu. Quem escreveu isso acertou a intenção e errou o formato — responder
    como se fosse falha soaria repreensão, e o menu já diz o que fazer.
    """
    condominio = _condominio_confirmado(conversa)
    opcao = _opcao(texto)

    # O escape em quem já está no menu é inofensivo: remostra.
    if opcao is None or opcao == ESCAPE:
        return Responder(mensagem=Mensagem.MENU)

    match opcao:
        case OpcaoMenu.DUVIDAS:
            return Responder(
                mensagem=Mensagem.CONVITE_PERGUNTA,
                transicao=Transicao.para_duvidas(condominio),
            )
        case OpcaoMenu.TROCAR_CONDOMINIO:
            return _recomecar_identificacao()
        case _ if opcao in _INDISPONIVEIS:
            return Responder(mensagem=Mensagem.OPCAO_INDISPONIVEL)
        case _:
            return Responder(mensagem=Mensagem.MENU_NAO_ENTENDIDO)


def _duvidas(conversa: Conversa, texto: str) -> Decisao:
    """Dentro do fluxo de perguntas — aqui quase tudo é pergunta.

    Só o 0 é comando. "45" e "Art. 45" seguem para a IA, porque em dúvidas o
    número faz parte da linguagem do regimento: quem digita 45 quer saber do
    artigo, não escolher opção de menu.

    Espaços em branco viram pedido de reformulação em vez de ir para a IA —
    gastaria um embedding e uma geração para não ter perguntado nada.
    """
    condominio = _condominio_confirmado(conversa)

    if _opcao(texto) == ESCAPE:
        return Responder(
            mensagem=Mensagem.MENU, transicao=Transicao.para_menu(condominio)
        )

    pergunta = texto.strip()
    if not pergunta:
        return Responder(mensagem=Mensagem.PERGUNTA_VAZIA)
    return DelegarDuvida(pergunta=pergunta)


def _condominio_confirmado(conversa: Conversa) -> UUID:
    """O condomínio da conversa, nos dois estados em que ele TEM que existir.

    Em menu e duvidas o chk_conversas_estado_coerente garante condominio_id
    preenchido, então chegar aqui com None significa que alguém rodou UPDATE na
    mão no banco ou que a constraint caiu. Falha alto em vez de adivinhar: um
    palpite aqui é exatamente o morador do condomínio A recebendo as regras do B.
    """
    if conversa.condominio_id is None:
        raise ValueError(
            f"conversa {conversa.id} em {conversa.estado.value} sem condominio_id — "
            "viola chk_conversas_estado_coerente"
        )
    return conversa.condominio_id
