"""Redação das mensagens fixas (função pura; nome distinto de mensagens.py, que
é o repositório de banco).

Identidades nascem aqui porque o roteador não pode emiti-las: a primeira
confirmação depende de I/O, e a contingência de uma falha. O Passo 2 fixou que
identidade que o roteador não emite é campo morto lá.

O menu é gerado a partir de OpcaoMenu, não escrito à mão: o número sai do enum,
então rótulo e destino não podem divergir em silêncio.
"""

from collections.abc import Sequence
from datetime import date
from enum import Enum

from areas import AreaReservavel
from condominios import CondominioElegivel
from minhas_reservas import MensagemMinhasReservas
from ocorrencia import (
    MAX_DESCRICAO,
    SEGUIR_SEM_FOTO,
    Anexo,
    MensagemOcorrencia,
    TipoSolicitacao,
)
from reserva import VER_MAIS, MensagemReserva, PaginaDias
from reservas import ReservaDoMorador
from roteador import Mensagem, OpcaoMenu


class MensagemAtendimento(str, Enum):
    CONFIRMAR_CONDOMINIO = "confirmar_condominio"
    CONTINGENCIA = "contingencia"
    SEM_CONDOMINIOS = "sem_condominios"
    REGRA_NAO_ENCONTRADA = "regra_nao_encontrada"


class MensagemSindico(str, Enum):
    """As únicas mensagens cujo destinatário NÃO é o morador.

    Moram aqui pelo mesmo motivo de MensagemAtendimento: não há motor de síndico
    que as emita — quem as produz é a casca.
    """

    AVISO_RESERVA = "aviso_reserva"
    AVISO_CANCELAMENTO = "aviso_cancelamento"
    AVISO_OCORRENCIA = "aviso_ocorrencia"
    SEM_CANAL = "sem_canal"


Identidade = (
    Mensagem
    | MensagemAtendimento
    | MensagemSindico
    | MensagemReserva
    | MensagemOcorrencia
    | MensagemMinhasReservas
)

_TIPOS_OCORRENCIA = {
    TipoSolicitacao.RECLAMACAO: "Reclamação",
    TipoSolicitacao.OCORRENCIA: "Ocorrência",
    TipoSolicitacao.MANUTENCAO: "Manutenção",
}

_ROTULOS = {
    OpcaoMenu.DUVIDAS: "Tirar dúvidas sobre o condomínio",
    OpcaoMenu.RESERVA: "Reservar área comum",
    OpcaoMenu.OCORRENCIA: "Abrir ocorrência",
    OpcaoMenu.SINDICO: "Falar com o síndico",
    OpcaoMenu.MINHAS_RESERVAS: "Minhas reservas",
    OpcaoMenu.TROCAR_CONDOMINIO: "Não sou desse condomínio",
}

_MENU = "Como posso ajudar?\n\n{opcoes}\n\nResponda com o número.".format(
    opcoes="\n".join(
        f"{opcao.value} - {rotulo}"
        for opcao, rotulo in sorted(_ROTULOS.items(), key=lambda par: par[0].value)
    )
)

_SIM_NAO = "1 - Sim\n2 - Não"

# LGPD: o pedido segue ao síndico com o número de quem pediu. A base legal é
# procedimento a pedido do titular, então basta transparência — não consentimento.
_TRANSPARENCIA = "O síndico recebe seu número e este pedido."

# Gerada do enum, como o menu: número e destino não podem divergir em silêncio.
_TELA_TIPOS = "\n".join(
    f"{posicao} - {rotulo}"
    for posicao, rotulo in enumerate(_TIPOS_OCORRENCIA.values(), start=1)
)

# Na reserva o "não" e o escape levam ao mesmo lugar, então a opção diz os dois.
_SIM_NAO_RESERVA = "1 - Sim\n2 - Não, voltar ao menu"
_SIM_NAO_CANCELAR = "1 - Sim, cancelar\n2 - Não, voltar ao menu"
_VOLTAR = "0 - Voltar ao menu"

_TRANSPARENCIA_RESERVA = "O síndico recebe seu número e esta reserva."
_DIA_LIBERADO = "O dia voltou a ficar livre."

# strftime("%a") devolve 'Sat': o locale do container é C.UTF-8 e pt_BR não está
# instalado (medido 27/07/2026). Tupla explícita, indexada por weekday().
_SEMANA = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
_SEMANA_LONGO = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)

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
    MensagemAtendimento.CONTINGENCIA: (
        "Tive um problema para processar sua mensagem. Pode tentar de novo?"
    ),
    MensagemAtendimento.SEM_CONDOMINIOS: (
        "Não encontrei condomínios disponíveis agora. Pode tentar mais tarde?"
    ),
    MensagemAtendimento.REGRA_NAO_ENCONTRADA: (
        "Não encontrei essa regra no regimento. Sugiro falar com o síndico."
    ),
    # Sem promessa de painel: ele não existe até a Fase 5, e o que o síndico
    # responder aqui ninguém lê ainda (a máquina de resposta é da Etapa 6).
    MensagemSindico.SEM_CANAL: (
        "Olá! Este número é o do síndico. Por aqui eu só envio os avisos de "
        "novos pedidos — ainda não consigo conversar."
    ),
    Mensagem.NADA_AGENDADO: f"Ok, não agendei nada.\n\n{_MENU}",
    Mensagem.NADA_REGISTRADO: f"Ok, não registrei nada.\n\n{_MENU}",
    Mensagem.NADA_CANCELADO: f"Ok, não cancelei nada.\n\n{_MENU}",
    MensagemMinhasReservas.SEM_RESERVAS: (
        f"Você não tem reserva ativa por aqui.\n\n{_MENU}"
    ),
    MensagemMinhasReservas.JA_NAO_ATIVA: (
        f"Essa reserva já não está ativa.\n\n{_MENU}"
    ),
    MensagemReserva.SEM_AREAS: (
        f"Esse condomínio ainda não tem área para reservar por aqui.\n\n{_MENU}"
    ),
    MensagemOcorrencia.ESCOLHER_TIPO: (
        f"O que você quer registrar?\n\n{_TELA_TIPOS}\n\n"
        f"Responda com o número.\n{_VOLTAR}"
    ),
    MensagemOcorrencia.TIPO_NAO_ENTENDIDO: (
        f"Não tenho essa opção.\n\n{_TELA_TIPOS}\n\n"
        f"Responda com o número.\n{_VOLTAR}"
    ),
    MensagemOcorrencia.PEDIR_DESCRICAO: (
        "Descreva o que aconteceu.\n\n"
        f"Se ajudar, pode mandar uma foto junto.\n\n{_VOLTAR}"
    ),
    MensagemOcorrencia.DESCRICAO_VAZIA: (
        f"Não recebi sua descrição. Pode escrever o que aconteceu?\n\n{_VOLTAR}"
    ),
    MensagemOcorrencia.DESCRICAO_LONGA: (
        f"Ficou comprido demais (o limite é {MAX_DESCRICAO} caracteres). "
        f"Pode resumir?\n\n{_VOLTAR}"
    ),
    MensagemOcorrencia.PEDIR_FOTO: (
        "Quer anexar uma foto?\n\n"
        f"Mande a foto, ou {SEGUIR_SEM_FOTO} - Seguir sem foto\n\n"
        f"A foto fica guardada junto do registro.\n{_VOLTAR}"
    ),
    MensagemOcorrencia.FOTO_NAO_ENTENDIDA: (
        "Não entendi.\n\n"
        f"Mande a foto, ou {SEGUIR_SEM_FOTO} - Seguir sem foto\n\n{_VOLTAR}"
    ),
    # Culpa do arquivo: mandar de novo o MESMO não resolve.
    MensagemOcorrencia.FOTO_RECUSADA: (
        "Não consegui aceitar essa foto — ela é grande demais ou não é uma "
        f"imagem.\n\nPode mandar outra, ou {SEGUIR_SEM_FOTO} - Seguir sem foto"
        f"\n\n{_VOLTAR}"
    ),
    # Culpa nossa ou da rede: a MESMA foto de novo tende a funcionar.
    MensagemOcorrencia.FOTO_FALHOU: (
        "Não consegui guardar a foto agora.\n\n"
        f"Pode mandar de novo, ou {SEGUIR_SEM_FOTO} - Seguir sem foto\n\n{_VOLTAR}"
    ),
}


def _numerar(rotulos: Sequence[str]) -> str:
    return "\n".join(f"{posicao} - {r}" for posicao, r in enumerate(rotulos, start=1))


def _curto(dia: date) -> str:
    return f"{_SEMANA[dia.weekday()]} {dia:%d/%m}"


def _longo(dia: date) -> str:
    return f"{_SEMANA_LONGO[dia.weekday()]}, {dia:%d/%m}"


def _tela_areas(areas: Sequence[AreaReservavel]) -> str:
    return (
        f"{_numerar([a.nome for a in areas])}\n\nResponda com o número.\n{_VOLTAR}"
    )


def _tela_dias(area: str, pagina: PaginaDias) -> str:
    """A lista e o "ver mais" saem das MESMAS constantes que o motor compara —
    número e rótulo não podem divergir em silêncio."""
    linhas = [f"{area} — escolha a data:", "", _numerar([_curto(d) for d in pagina.dias])]
    if not pagina.ultima:
        linhas += ["", f"{VER_MAIS} - Ver mais datas"]
    linhas += ["", _VOLTAR]
    return "\n".join(linhas)


def _tela_confirmar(area: str, dia: date) -> str:
    return f"Confirmar a reserva?\n\n{area}\n{_longo(dia)}\n\n{_SIM_NAO_RESERVA}"


def _tela_minhas(reservas: Sequence[ReservaDoMorador]) -> str:
    linhas = _numerar([f"{r.area} — {_curto(r.dia)}" for r in reservas])
    return (
        f"Suas reservas:\n\n{linhas}\n\n"
        f"Responda com o número para cancelar.\n{_VOLTAR}"
    )


def _tela_cancelar(area: str, dia: date) -> str:
    return f"Cancelar esta reserva?\n\n{area}\n{_longo(dia)}\n\n{_SIM_NAO_CANCELAR}"


def _resumo_ocorrencia(tipo: TipoSolicitacao, descricao: str,
                       anexos: Sequence[Anexo]) -> str:
    """O que a confirmação recita. Sem anexo a linha some — não anunciamos vazio."""
    linhas = [_TIPOS_OCORRENCIA[tipo], descricao]
    if anexos:
        linhas.append(f"({len(anexos)} foto anexada)" if len(anexos) == 1
                      else f"({len(anexos)} fotos anexadas)")
    return "\n".join(linhas)


def renderizar(
    identidade: Identidade,
    *,
    condominios: Sequence[CondominioElegivel] = (),
    nome_condominio: str | None = None,
    areas: Sequence[AreaReservavel] = (),
    area: str | None = None,
    pagina: PaginaDias | None = None,
    dia: date | None = None,
    aviso: MensagemReserva | None = None,
    reservas: Sequence[ReservaDoMorador] = (),
    tipo: TipoSolicitacao | None = None,
    descricao: str | None = None,
    anexos: Sequence[Anexo] = (),
    identificador: str | None = None,
    telefone_morador: str | None = None,
) -> str:
    """Traduz a identidade da mensagem no texto que o morador recebe.

    Contexto faltando levanta: é bug de chamada, não entrada do morador.
    """
    match identidade:
        case MensagemReserva.ESCOLHER_AREA:
            return (
                "Qual área você quer reservar?\n\n"
                f"{_tela_areas(_exigir(areas, 'a lista de áreas'))}"
            )
        case MensagemReserva.AREA_NAO_ENTENDIDA:
            return (
                "Não tenho essa área.\n\n"
                f"{_tela_areas(_exigir(areas, 'a lista de áreas'))}"
            )
        case MensagemReserva.LISTA_DIAS:
            corpo = _tela_dias(_exigir(area, "o nome da área"), _exigir(pagina, "a página"))
            return f"{_PREFIXOS[aviso]}{corpo}" if aviso else corpo
        case MensagemReserva.DIA_NAO_ENTENDIDO:
            corpo = _tela_dias(_exigir(area, "o nome da área"), _exigir(pagina, "a página"))
            return f"Não tenho essa data.\n\n{corpo}"
        case MensagemReserva.ULTIMAS_DATAS:
            corpo = _tela_dias(_exigir(area, "o nome da área"), _exigir(pagina, "a página"))
            return f"Essas são as últimas datas que consigo agendar.\n\n{corpo}"
        case MensagemReserva.SEM_DATAS:
            return (
                f"{_exigir(area, 'o nome da área')} está sem data livre no período "
                f"que consigo agendar.\n\n{_MENU}"
            )
        case MensagemReserva.CONFIRMAR:
            return _tela_confirmar(
                _exigir(area, "o nome da área"), _exigir(dia, "a data")
            )
        case MensagemReserva.CONFIRMACAO_NAO_ENTENDIDA:
            corpo = _tela_confirmar(
                _exigir(area, "o nome da área"), _exigir(dia, "a data")
            )
            return f"Só preciso de 1 ou 2.\n\n{corpo}"
        case MensagemReserva.RESERVA_CONFIRMADA:
            return (
                "Pronto! Sua reserva está confirmada:\n\n"
                f"{_exigir(area, 'o nome da área')}\n{_longo(_exigir(dia, 'a data'))}"
                f"\n\nSe precisar desmarcar, escolha "
                f"{OpcaoMenu.MINHAS_RESERVAS.value} - "
                f"{_ROTULOS[OpcaoMenu.MINHAS_RESERVAS]}."
                f"\n{_TRANSPARENCIA_RESERVA}\n\n{_MENU}"
            )
        case MensagemMinhasReservas.LISTA:
            return _tela_minhas(_exigir(reservas, "a lista de reservas"))
        case MensagemMinhasReservas.RESERVA_NAO_ENTENDIDA:
            corpo = _tela_minhas(_exigir(reservas, "a lista de reservas"))
            return f"Não tenho essa reserva.\n\n{corpo}"
        case MensagemMinhasReservas.CONFIRMAR:
            return _tela_cancelar(
                _exigir(area, "o nome da área"), _exigir(dia, "a data")
            )
        case MensagemMinhasReservas.CONFIRMACAO_NAO_ENTENDIDA:
            corpo = _tela_cancelar(
                _exigir(area, "o nome da área"), _exigir(dia, "a data")
            )
            return f"Só preciso de 1 ou 2.\n\n{corpo}"
        case MensagemMinhasReservas.CANCELADA:
            return (
                "Pronto, cancelei sua reserva:\n\n"
                f"{_exigir(area, 'o nome da área')}\n{_longo(_exigir(dia, 'a data'))}"
                f"\n\n{_DIA_LIBERADO}\n\n{_MENU}"
            )
        case MensagemOcorrencia.CONFIRMAR:
            return (
                "Confirmar o registro?\n\n"
                f"{_resumo(tipo, descricao, anexos)}\n\n{_SIM_NAO_RESERVA}"
            )
        case MensagemOcorrencia.CONFIRMACAO_NAO_ENTENDIDA:
            return (
                "Só preciso de 1 ou 2.\n\nConfirmar o registro?\n\n"
                f"{_resumo(tipo, descricao, anexos)}\n\n{_SIM_NAO_RESERVA}"
            )
        case MensagemOcorrencia.REGISTRADA:
            return (
                f"Pronto! Registrei:\n\n{_resumo(tipo, descricao, anexos)}"
                f"\n{_TRANSPARENCIA}\n\n{_MENU}"
            )
        case MensagemSindico.AVISO_RESERVA:
            return (
                f"Nova reserva #{_exigir(identificador, 'o identificador')}\n\n"
                f"{_exigir(area, 'o nome da área')}\n"
                f"{_longo(_exigir(dia, 'a data'))}\n\n"
                f"Morador: {_exigir(telefone_morador, 'o telefone do morador')}"
            )
        case MensagemSindico.AVISO_CANCELAMENTO:
            return (
                f"Reserva cancelada #{_exigir(identificador, 'o identificador')}\n\n"
                f"{_exigir(area, 'o nome da área')}\n"
                f"{_longo(_exigir(dia, 'a data'))}\n\n"
                f"Morador: {_exigir(telefone_morador, 'o telefone do morador')}\n\n"
                f"{_DIA_LIBERADO}"
            )
        case MensagemSindico.AVISO_OCORRENCIA:
            return (
                f"Ocorrência #{_exigir(identificador, 'o identificador')}\n\n"
                f"{_resumo(tipo, descricao, anexos)}\n\n"
                f"Morador: {_exigir(telefone_morador, 'o telefone do morador')}\n\n"
                "Registrada no painel."
            )
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


_PREFIXOS = {
    MensagemReserva.DATA_TOMADA: (
        "Essa data acabou de ser reservada por outro morador.\n"
        "Veja as datas atualizadas:\n\n"
    )
}


def _exigir_lista(condominios: Sequence[CondominioElegivel]) -> str:
    if not condominios:
        raise ValueError("mensagem exige a lista de condomínios, e ela veio vazia")
    return _numerar([c.nome for c in condominios])


def _exigir_nome(nome: str | None) -> str:
    if not nome:
        raise ValueError("mensagem exige o nome do condomínio, e ele veio vazio")
    return nome


def _exigir(valor, oque: str):
    """Contexto da reserva que a mensagem cita e não chegou."""
    if valor is None or (hasattr(valor, "__len__") and not len(valor)):
        raise ValueError(f"mensagem da reserva exige {oque}, e veio vazio")
    return valor


def _resumo(tipo, descricao, anexos: Sequence[Anexo]) -> str:
    """Anexos podem ser vazios legitimamente; tipo e descrição, nunca."""
    return _resumo_ocorrencia(
        _exigir(tipo, "o tipo"), _exigir(descricao, "a descrição"), anexos
    )
