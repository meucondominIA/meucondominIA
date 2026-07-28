"""Testes da redação (Fase 3 · Passo 3).

Função pura: sem I/O, determinística. Provamos os invariantes de produto (nada
promete prazo; o menu vem do enum, não de string solta; a lista é numerada a
partir de 1) e o contrato (contexto faltando levanta, porque é bug de chamada).
"""

from datetime import date
from uuid import uuid4

import pytest

from areas import AreaReservavel
from condominios import CondominioElegivel
from reserva import MensagemReserva, montar_pagina
from roteador import Mensagem, OpcaoMenu
from textos import MensagemAtendimento, renderizar

LISTA = [
    CondominioElegivel(id=uuid4(), nome="Edifício Alfa"),
    CondominioElegivel(id=uuid4(), nome="Edifício Beta"),
]
AREAS = [AreaReservavel(id=uuid4(), nome="Salão de Festas")]
PAGINA = montar_pagina([date(2026, 8, d) for d in range(1, 16)], pagina=0)

_CONTEXTO = dict(
    condominios=LISTA,
    nome_condominio="Edifício Alfa",
    areas=AREAS,
    area="Salão de Festas",
    pagina=PAGINA,
    dia=date(2026, 8, 1),
)

_SEM_CONTEXTO = [
    Mensagem.MENU,
    Mensagem.MENU_NAO_ENTENDIDO,
    Mensagem.OPCAO_INDISPONIVEL,
    Mensagem.CONVITE_PERGUNTA,
    Mensagem.PERGUNTA_VAZIA,
    Mensagem.SO_ENTENDO_TEXTO,
    Mensagem.NADA_AGENDADO,
    MensagemAtendimento.CONTINGENCIA,
    MensagemAtendimento.SEM_CONDOMINIOS,
    MensagemReserva.SEM_AREAS,
]

# DATA_TOMADA é PREFIXO de LISTA_DIAS, não tela: não renderiza sozinha.
_PREFIXO = {MensagemReserva.DATA_TOMADA}


def _todas_as_identidades():
    return [
        i
        for i in list(Mensagem) + list(MensagemAtendimento) + list(MensagemReserva)
        if i not in _PREFIXO
    ]


@pytest.mark.parametrize("identidade", _todas_as_identidades())
def test_toda_identidade_tem_redacao(identidade):
    """O inverso do teste do roteador: identidade sem texto é buraco de catálogo."""
    texto = renderizar(identidade, **_CONTEXTO)
    assert isinstance(texto, str) and texto.strip()


def test_identidades_nao_colidem_entre_os_enums():
    """str enums de valor igual são == e têm o mesmo hash: o braço errado do
    match capturaria a mensagem (CONFIRMACAO_NAO_ENTENDIDA existe nos dois)."""
    valores = [
        i.value for i in list(Mensagem) + list(MensagemAtendimento) + list(MensagemReserva)
    ]
    assert len(valores) == len(set(valores))


@pytest.mark.parametrize("identidade", _todas_as_identidades())
def test_nenhuma_promete_prazo(identidade):
    """Decisão de produto: nada promete quando um recurso ausente vai chegar.

    A lista é só de PROMESSA. "dia"/"próxim" saíram daqui em 27/07: o wizard de
    reserva fala de datas o tempo todo, e proibir a palavra censurava a redação
    em vez de proteger o invariante. Onde eles importam há teste dirigido.
    """
    texto = renderizar(identidade, **_CONTEXTO).lower()
    for proibido in ("em breve", "logo", "prazo", "aguarde"):
        assert proibido not in texto


@pytest.mark.parametrize(
    "identidade",
    [
        Mensagem.OPCAO_INDISPONIVEL,
        Mensagem.MENU_NAO_ENTENDIDO,
        MensagemAtendimento.SEM_CONDOMINIOS,
        MensagemAtendimento.CONTINGENCIA,
    ],
)
def test_indisponivel_nao_insinua_data(identidade):
    """Aqui sim: quem fala de recurso ausente não pode sugerir quando ele chega."""
    texto = renderizar(identidade, **_CONTEXTO).lower()
    for proibido in ("próxim", "dia", "semana", "mês"):
        assert proibido not in texto


@pytest.mark.parametrize("identidade", _SEM_CONTEXTO)
def test_constantes_nao_exigem_contexto(identidade):
    """O que não cita lista nem condomínio renderiza sem argumento nenhum."""
    assert renderizar(identidade).strip()


def test_menu_lista_os_rotulos_na_ordem_do_enum():
    texto = renderizar(Mensagem.MENU)
    for opcao in OpcaoMenu:
        assert f"{opcao.value} -" in texto
    assert "1 - Tirar dúvidas sobre o condomínio" in texto
    assert "9 - Não sou desse condomínio" in texto
    # 5..8 não têm dono: não aparecem no menu
    for reservado in ("5 -", "6 -", "7 -", "8 -"):
        assert reservado not in texto


def test_menu_numero_bate_com_o_destino_do_roteador():
    """Prova o que os testes do roteador não pegam: o rótulo certo no número certo.

    Se alguém trocar RESERVA e SINDICO de posição no enum, a suíte do roteador
    segue verde (o conjunto _INDISPONIVEIS não muda) — mas o morador veria o menu
    errado. Aqui não passa."""
    texto = renderizar(Mensagem.MENU)
    esperado = {
        OpcaoMenu.DUVIDAS: "Tirar dúvidas sobre o condomínio",
        OpcaoMenu.RESERVA: "Reservar área comum",
        OpcaoMenu.OCORRENCIA: "Abrir ocorrência",
        OpcaoMenu.SINDICO: "Falar com o síndico",
        OpcaoMenu.TROCAR_CONDOMINIO: "Não sou desse condomínio",
    }
    for opcao, rotulo in esperado.items():
        assert f"{opcao.value} - {rotulo}" in texto


def test_lista_numerada_a_partir_de_1():
    texto = renderizar(Mensagem.PEDIR_CONDOMINIO, condominios=LISTA)
    assert "1 - Edifício Alfa" in texto
    assert "2 - Edifício Beta" in texto
    assert "0 -" not in texto


def test_confirmacao_cita_o_nome():
    texto = renderizar(
        MensagemAtendimento.CONFIRMAR_CONDOMINIO, nome_condominio="Edifício Alfa"
    )
    assert "Edifício Alfa" in texto
    assert "1 - Sim" in texto and "2 - Não" in texto


def test_reconfirmacao_cita_o_nome_e_soa_como_reencontro():
    texto = renderizar(
        Mensagem.RECONFIRMAR_CONDOMINIO, nome_condominio="Edifício Alfa"
    )
    assert "Edifício Alfa" in texto
    assert "1 - Sim" in texto and "2 - Não" in texto


@pytest.mark.parametrize(
    "identidade",
    [Mensagem.PEDIR_CONDOMINIO, Mensagem.CONDOMINIO_NAO_ENTENDIDO],
)
def test_lista_vazia_levanta_e_nao_gera_menu_quebrado(identidade):
    with pytest.raises(ValueError, match="lista de condomínios"):
        renderizar(identidade, condominios=[])


@pytest.mark.parametrize(
    "identidade",
    [
        MensagemAtendimento.CONFIRMAR_CONDOMINIO,
        Mensagem.RECONFIRMAR_CONDOMINIO,
        Mensagem.CONFIRMACAO_NAO_ENTENDIDA,
    ],
)
def test_nome_faltando_levanta(identidade):
    with pytest.raises(ValueError, match="nome do condomínio"):
        renderizar(identidade, nome_condominio=None)
