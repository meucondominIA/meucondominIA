"""Máquina de estados do atendimento (Fase 3 · Passo 2).

Ponto ÚNICO de decisão: dado o estado da conversa e a mensagem, decide o que
fazer. Decide, não executa — nada aqui toca banco, rede ou relógio.

`def` e não `async def` de propósito: função síncrona não consegue `await`, logo
não consegue tocar I/O nem por acidente nem daqui a seis meses. A pureza vira
propriedade da linguagem em vez de combinado, como o CHECK faz no banco.

Tudo é número. A confirmação é 1/2 como o menu é 1..4, e o escape é 0 — que fica
estruturalmente fora da faixa das listas (que começam em 1), então nunca colide
por mais que o menu cresça. Some daqui qualquer comparação de palavra: não há
'sim', 'não' nem sinônimo a adivinhar, e por isso não há acento a normalizar.

A saída é o 9, fora da faixa dos serviços: no fim da lista ela se deslocaria a
cada serviço novo.

Uma decisão = UMA mensagem. Não é preferência: uq_mensagens_em_resposta_a é
único, então o banco só aceita uma resposta por mensagem recebida.

A decisão nomeia a mensagem (Mensagem.MENU), não escreve o texto — a redação é
do passo 3. E quatro decisões DELEGAM, porque não podem ser resolvidas sem I/O:
qual condomínio é o item N exige a lista (passo 3), responder dúvida exige
RAG + LLM (passo 6), a reserva exige áreas e agenda, e a ocorrência exige a
escrita (e o upload da foto, se houver).

Transicao só nasce pelos construtores nomeados: a trinca ilegal fica
irrepresentável aqui, do mesmo jeito que chk_conversas_estado_coerente a torna
ingravável no banco. O CHECK continua sendo a garantia — isto é conveniência
para quem chama, não segunda validação.
"""

import re
from datetime import datetime
from enum import Enum, IntEnum
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from zpro_models import MessageType


class Estado(str, Enum):
    """Espelha chk_conversas_estado. Mudar aqui exige migration."""

    IDENTIFICACAO = "identificacao"
    AGUARDANDO_CONFIRMACAO = "aguardando_confirmacao"
    MENU = "menu"
    DUVIDAS = "duvidas"
    RESERVA = "reserva"
    OCORRENCIA = "ocorrencia"
    MINHAS_RESERVAS = "minhas_reservas"


class Mensagem(str, Enum):
    """Identidade da mensagem; a redação é do passo 3."""

    PEDIR_CONDOMINIO = "pedir_condominio"
    CONDOMINIO_NAO_ENTENDIDO = "condominio_nao_entendido"
    RECONFIRMAR_CONDOMINIO = "reconfirmar_condominio"
    CONFIRMACAO_NAO_ENTENDIDA = "confirmacao_nao_entendida"
    MENU = "menu"
    MENU_NAO_ENTENDIDO = "menu_nao_entendido"
    OPCAO_INDISPONIVEL = "opcao_indisponivel"
    CONVITE_PERGUNTA = "convite_pergunta"
    PERGUNTA_VAZIA = "pergunta_vazia"
    SO_ENTENDO_TEXTO = "so_entendo_texto"
    NADA_AGENDADO = "nada_agendado"
    NADA_REGISTRADO = "nada_registrado"
    NADA_CANCELADO = "nada_cancelado"


ESCAPE = 0


class OpcaoConfirmacao(IntEnum):
    SIM = 1
    NAO = 2


class OpcaoMenu(IntEnum):
    """Destinos do menu. Os rótulos são do passo 3; o roteamento é daqui.

    Numeração TRAVADA (21/07/2026): só itens com tabela no schema. 5..8 ficam
    livres para o que vier, sem empurrar ninguém.
    """

    DUVIDAS = 1
    RESERVA = 2
    OCORRENCIA = 3
    SINDICO = 4
    MINHAS_RESERVAS = 5
    TROCAR_CONDOMINIO = 9


_INDISPONIVEIS = frozenset({OpcaoMenu.SINDICO})

# Estados com tenant confirmado — mesmo ramo do chk_conversas_estado_coerente.
# Fluxo esquecido aqui não reconfirma sessão expirada: escreve no tenant antigo.
_COM_TENANT = frozenset(
    {
        Estado.MENU,
        Estado.DUVIDAS,
        Estado.RESERVA,
        Estado.OCORRENCIA,
        Estado.MINHAS_RESERVAS,
    }
)

# Onde imagem é entrada legítima. Fora daqui segue ouvindo SO_ENTENDO_TEXTO.
_ACEITAM_IMAGEM = frozenset({Estado.OCORRENCIA})

# Tolera o que as pessoas digitam de verdade: espaço em volta, "1." e "1)"
# copiados do formato da lista, zero à esquerda. Por extenso não entra — abriria
# a porta que a escolha por número fechou.
_OPCAO = re.compile(r"\s*(\d{1,2})[.)]?\s*")


class Transicao(BaseModel):
    """O destino completo — os CHECKs do banco são sobre as quatro colunas juntas.

    `rascunho` é dict, não modelo tipado: aqui `reserva` é opaca, quem conhece a
    forma é o motor. Default None porque os outros estados EXIGEM null
    (chk_conversas_rascunho); só para_reserva o pede, e sem default.
    """

    model_config = ConfigDict(frozen=True)

    estado: Estado
    condominio_id: UUID | None
    condominio_pendente: UUID | None
    rascunho: dict[str, Any] | None = None

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

    @classmethod
    def para_reserva(cls, condominio: UUID, rascunho: dict[str, Any]) -> Self:
        return cls(
            estado=Estado.RESERVA,
            condominio_id=condominio,
            condominio_pendente=None,
            rascunho=rascunho,
        )

    @classmethod
    def para_ocorrencia(cls, condominio: UUID, rascunho: dict[str, Any]) -> Self:
        return cls(
            estado=Estado.OCORRENCIA,
            condominio_id=condominio,
            condominio_pendente=None,
            rascunho=rascunho,
        )

    @classmethod
    def para_minhas_reservas(cls, condominio: UUID, rascunho: dict[str, Any]) -> Self:
        return cls(
            estado=Estado.MINHAS_RESERVAS,
            condominio_id=condominio,
            condominio_pendente=None,
            rascunho=rascunho,
        )


class Conversa(BaseModel):
    """O que o roteador precisa saber da conversa — espelha as colunas.

    Três colunas o roteador NÃO lê, e existem para a costura: o relógio é do
    atendimento, `telefone` vai para a reserva e `rascunho` para o motor.

    Sem default nas duas novas de propósito: um default esconderia RETURNING
    incompleto, e rascunho ausente reiniciaria o wizard em silêncio.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    estado: Estado
    condominio_id: UUID | None
    condominio_pendente: UUID | None
    ultima_interacao_em: datetime
    telefone: str
    rascunho: dict[str, Any] | None


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


class DelegarReserva(BaseModel):
    """O morador entrou ou avançou no wizard; quem interpreta é o motor.

    Não carrega passo nem dados: o roteador sabe que está DENTRO da reserva, não
    onde. O passo vive no rascunho.
    """

    model_config = ConfigDict(frozen=True)

    escolha: str


class DelegarOcorrencia(BaseModel):
    """O morador entrou ou avançou no wizard de ocorrência.

    `tem_foto` é marcador, não bytes: o roteador é puro. Quem carrega a mídia é a
    casca, que decide o upload.
    """

    model_config = ConfigDict(frozen=True)

    texto: str | None
    tem_foto: bool = False


class DelegarMinhasReservas(BaseModel):
    """O morador abriu ou avançou em "Minhas reservas"; quem interpreta é o motor.

    Como o DelegarReserva: o roteador sabe que está DENTRO do fluxo, não onde.
    """

    model_config = ConfigDict(frozen=True)

    escolha: str


Decisao = (
    Responder
    | DelegarIdentificacao
    | DelegarDuvida
    | DelegarReserva
    | DelegarOcorrencia
    | DelegarMinhasReservas
)


def opcao(texto: str) -> int | None:
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


def _reconfirmar(conversa: Conversa) -> Responder:
    """O tenant confirmado volta a candidato: enquanto não confirmar, nenhuma
    busca responde pelo condomínio antigo."""
    return Responder(
        mensagem=Mensagem.RECONFIRMAR_CONDOMINIO,
        transicao=Transicao.para_confirmacao(_condominio_confirmado(conversa)),
    )


def rotear(
    conversa: Conversa,
    *,
    tipo: MessageType,
    texto: str | None,
    precisa_reconfirmar: bool = False,
) -> Decisao:
    """Porta única: toda mensagem do morador entra aqui e sai como decisão.

    A ordem das guardas importa: sem candidato sai de lá mesmo por áudio; e a
    reconfirmação vem depois do filtro de tipo, senão pedir "1 ou 2" a quem
    mandou áudio giraria em círculo.

    `precisa_reconfirmar` chega medido de fora — o roteador não olha relógio.
    """
    if (
        conversa.estado is Estado.AGUARDANDO_CONFIRMACAO
        and conversa.condominio_pendente is None
    ):
        return _recomecar_identificacao()

    imagem_aceita = (
        tipo is MessageType.IMAGE and conversa.estado in _ACEITAM_IMAGEM
    )
    if not imagem_aceita and (tipo is not MessageType.TEXT or texto is None):
        return Responder(mensagem=Mensagem.SO_ENTENDO_TEXTO)

    if precisa_reconfirmar and conversa.estado in _COM_TENANT:
        return _reconfirmar(conversa)

    match conversa.estado:
        case Estado.IDENTIFICACAO:
            return _identificacao(texto)
        case Estado.AGUARDANDO_CONFIRMACAO:
            return _confirmacao(conversa, texto)
        case Estado.MENU:
            return _menu(conversa, texto)
        case Estado.DUVIDAS:
            return _duvidas(conversa, texto)
        case Estado.RESERVA:
            return _reserva(conversa, texto)
        case Estado.OCORRENCIA:
            return _ocorrencia(conversa, tipo, texto)
        case Estado.MINHAS_RESERVAS:
            return _minhas_reservas(conversa, texto)


def _identificacao(texto: str) -> Decisao:
    """Primeiro contato: o sistema mandou a lista de condomínios e espera o número.

    "2" vira delegação — o roteador NÃO sabe se existe um segundo condomínio,
    porque isso exige ler o banco. Quem confere é o passo 3.

    "moro no Gabro", "   " e "0" caem todos no mesmo lugar: pedir o número de
    novo. O 0 é escape nos outros estados, mas aqui não existe menu para voltar,
    e prometer um atalho que não leva a lugar nenhum é pior do que ignorá-lo.
    """
    escolhido = opcao(texto)
    if escolhido is None or escolhido < 1:
        return Responder(mensagem=Mensagem.CONDOMINIO_NAO_ENTENDIDO)
    return DelegarIdentificacao(indice=escolhido)


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

    match opcao(texto):
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

    O 1 abre dúvidas (a única que chama IA); 2 e 3 abrem os wizards, que são
    determinísticos. O 4 responde "indisponível" sem prometer prazo. O 9 é a
    saída de quem não é (ou deixou de ser) desse condomínio.

    5..8 são reservados e ainda sem dono: caem em MENU_NAO_ENTENDIDO, que fala
    do sistema ("não tenho essa opção") e não do morador.

    "quero falar com o síndico" digitado por extenso NÃO vira erro: remostra o
    menu. Quem escreveu isso acertou a intenção e errou o formato — responder
    como se fosse falha soaria repreensão, e o menu já diz o que fazer.
    """
    condominio = _condominio_confirmado(conversa)
    escolhido = opcao(texto)

    # O escape em quem já está no menu é inofensivo: remostra.
    if escolhido is None or escolhido == ESCAPE:
        return Responder(mensagem=Mensagem.MENU)

    match escolhido:
        case OpcaoMenu.DUVIDAS:
            return Responder(
                mensagem=Mensagem.CONVITE_PERGUNTA,
                transicao=Transicao.para_duvidas(condominio),
            )
        case OpcaoMenu.RESERVA:
            return DelegarReserva(escolha=texto)
        case OpcaoMenu.OCORRENCIA:
            return DelegarOcorrencia(texto=texto)
        case OpcaoMenu.MINHAS_RESERVAS:
            return DelegarMinhasReservas(escolha=texto)
        case OpcaoMenu.TROCAR_CONDOMINIO:
            return _recomecar_identificacao()
        case _ if escolhido in _INDISPONIVEIS:
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

    if opcao(texto) == ESCAPE:
        return Responder(
            mensagem=Mensagem.MENU, transicao=Transicao.para_menu(condominio)
        )

    pergunta = texto.strip()
    if not pergunta:
        return Responder(mensagem=Mensagem.PERGUNTA_VAZIA)
    return DelegarDuvida(pergunta=pergunta)


def _reserva(conversa: Conversa, texto: str) -> Decisao:
    """Dentro do wizard — aqui quase tudo é escolha de tela.

    Só o 0 é comando, e é resolvido AQUI: é a saída que precisa funcionar mesmo
    com o rascunho ilegível ou a leitura de áreas falhando. Nada foi gravado até
    a confirmação, então sair não cancela nada — só não agenda.

    Todo o resto delega: o roteador não sabe qual passo é, e não deve saber.
    """
    condominio = _condominio_confirmado(conversa)

    if opcao(texto) == ESCAPE:
        return Responder(
            mensagem=Mensagem.NADA_AGENDADO,
            transicao=Transicao.para_menu(condominio),
        )
    return DelegarReserva(escolha=texto)


def _minhas_reservas(conversa: Conversa, texto: str) -> Decisao:
    """Dentro do fluxo — aqui quase tudo é escolha de tela.

    Só o 0 é comando, e é resolvido AQUI: é a saída que precisa funcionar mesmo
    com o rascunho ilegível. Sair não desfaz nada — o cancelamento só acontece
    depois do 1 na confirmação.
    """
    condominio = _condominio_confirmado(conversa)

    if opcao(texto) == ESCAPE:
        return Responder(
            mensagem=Mensagem.NADA_CANCELADO,
            transicao=Transicao.para_menu(condominio),
        )
    return DelegarMinhasReservas(escolha=texto)


def _ocorrencia(conversa: Conversa, tipo: MessageType, texto: str | None) -> Decisao:
    """Dentro do wizard — e o único estado onde imagem entra.

    Só o 0 é comando, resolvido AQUI: é a saída que precisa funcionar mesmo com o
    rascunho ilegível. Nada foi gravado em solicitacoes até a confirmação.

    Foto nunca é escape: `texto` dela é legenda, e uma legenda "0" não deve tirar
    o morador do wizard.
    """
    condominio = _condominio_confirmado(conversa)
    tem_foto = tipo is MessageType.IMAGE

    if not tem_foto and opcao(texto or "") == ESCAPE:
        return Responder(
            mensagem=Mensagem.NADA_REGISTRADO,
            transicao=Transicao.para_menu(condominio),
        )
    return DelegarOcorrencia(texto=texto, tem_foto=tem_foto)


def _condominio_confirmado(conversa: Conversa) -> UUID:
    """O condomínio da conversa, nos estados em que ele TEM que existir.

    Em menu, duvidas e reserva o chk_conversas_estado_coerente garante condominio_id
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
