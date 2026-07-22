"""Redação das mensagens fixas (função pura; nome distinto de mensagens.py, que
é o repositório de banco).

Três identidades nascem aqui porque o roteador não pode emiti-las: a primeira
confirmação e a provisória de dúvidas dependem de I/O, e a contingência de uma
falha. O Passo 2 fixou que identidade que o roteador não emite é campo morto lá.

O menu é gerado a partir de OpcaoMenu, não escrito à mão: o número sai do enum,
então rótulo e destino não podem divergir em silêncio.
"""

from collections.abc import Sequence
from enum import Enum

from condominios import CondominioElegivel
from roteador import Mensagem, OpcaoMenu


class MensagemAtendimento(str, Enum):
    CONFIRMAR_CONDOMINIO = "confirmar_condominio"
    DUVIDAS_PROVISORIA = "duvidas_provisoria"
    CONTINGENCIA = "contingencia"
    SEM_CONDOMINIOS = "sem_condominios"


Identidade = Mensagem | MensagemAtendimento

_ROTULOS = {
    OpcaoMenu.DUVIDAS: "Tirar dúvidas sobre o condomínio",
    OpcaoMenu.RESERVA: "Reservar área comum",
    OpcaoMenu.OCORRENCIA: "Abrir ocorrência",
    OpcaoMenu.SINDICO: "Falar com o síndico",
    OpcaoMenu.TROCAR_CONDOMINIO: "Não sou desse condomínio",
}

_MENU = "Como posso ajudar?\n\n{opcoes}\n\nResponda com o número.".format(
    opcoes="\n".join(
        f"{opcao.value} - {rotulo}"
        for opcao, rotulo in sorted(_ROTULOS.items(), key=lambda par: par[0].value)
    )
)

_SIM_NAO = "1 - Sim\n2 - Não"

_CONSTANTES: dict[Identidade, str] = {
    Mensagem.MENU: _MENU,
    Mensagem.MENU_NAO_ENTENDIDO: f"Não tenho essa opção.\n\n{_MENU}",
    Mensagem.OPCAO_INDISPONIVEL: (
        f"Essa opção ainda não está disponível por aqui.\n\n{_MENU}"
    ),
    Mensagem.CONVITE_PERGUNTA: (
        "Pode perguntar sobre as regras do condomínio.\n\n"
        "Digite 0 para voltar ao menu."
    ),
    Mensagem.PERGUNTA_VAZIA: "Não recebi sua pergunta. Pode escrever de novo?",
    Mensagem.SO_ENTENDO_TEXTO: (
        "Recebi sua mensagem, mas por enquanto só entendo texto."
    ),
    MensagemAtendimento.DUVIDAS_PROVISORIA: (
        "Ainda não consigo responder perguntas sobre o regimento.\n\n"
        "Digite 0 para voltar ao menu."
    ),
    MensagemAtendimento.CONTINGENCIA: (
        "Tive um problema para processar sua mensagem. Pode tentar de novo?"
    ),
    MensagemAtendimento.SEM_CONDOMINIOS: (
        "Não encontrei condomínios disponíveis agora. Pode tentar mais tarde?"
    ),
}


def _numerar(condominios: Sequence[CondominioElegivel]) -> str:
    return "\n".join(
        f"{posicao} - {c.nome}" for posicao, c in enumerate(condominios, start=1)
    )


def renderizar(
    identidade: Identidade,
    *,
    condominios: Sequence[CondominioElegivel] = (),
    nome_condominio: str | None = None,
) -> str:
    """Traduz a identidade da mensagem no texto que o morador recebe.

    Contexto faltando levanta: é bug de chamada, não entrada do morador.
    """
    match identidade:
        case Mensagem.PEDIR_CONDOMINIO:
            return (
                "Olá! Para eu te ajudar, escolha o seu condomínio:\n\n"
                f"{_exigir_lista(condominios)}\n\nResponda com o número."
            )
        case Mensagem.CONDOMINIO_NAO_ENTENDIDO:
            return (
                "Preciso do número do condomínio:\n\n"
                f"{_exigir_lista(condominios)}\n\nResponda com o número."
            )
        case MensagemAtendimento.CONFIRMAR_CONDOMINIO:
            return f"É o {_exigir_nome(nome_condominio)}?\n\n{_SIM_NAO}"
        case Mensagem.RECONFIRMAR_CONDOMINIO:
            return (
                "Olá de novo! Só para confirmar: você fala do "
                f"{_exigir_nome(nome_condominio)}?\n\n{_SIM_NAO}"
            )
        case Mensagem.CONFIRMACAO_NAO_ENTENDIDA:
            return (
                f"Só preciso de 1 ou 2. É o {_exigir_nome(nome_condominio)}?"
                f"\n\n{_SIM_NAO}"
            )
        case _:
            return _CONSTANTES[identidade]


def _exigir_lista(condominios: Sequence[CondominioElegivel]) -> str:
    if not condominios:
        raise ValueError("mensagem exige a lista de condomínios, e ela veio vazia")
    return _numerar(condominios)


def _exigir_nome(nome: str | None) -> str:
    if not nome:
        raise ValueError("mensagem exige o nome do condomínio, e ele veio vazio")
    return nome
