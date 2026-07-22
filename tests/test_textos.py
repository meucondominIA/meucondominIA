"""Testes da redação (Fase 3 · Passo 3).

Função pura: sem I/O, determinística. Provamos os invariantes de produto (nada
promete prazo; o menu vem do enum, não de string solta; a lista é numerada a
partir de 1) e o contrato (contexto faltando levanta, porque é bug de chamada).
"""

from uuid import uuid4

import pytest

from condominios import CondominioElegivel
from roteador import Mensagem, OpcaoMenu
from textos import MensagemAtendimento, renderizar

LISTA = [
    CondominioElegivel(id=uuid4(), nome="Edifício Alfa"),
    CondominioElegivel(id=uuid4(), nome="Edifício Beta"),
]

_SEM_CONTEXTO = [
    Mensagem.MENU,
    Mensagem.MENU_NAO_ENTENDIDO,
    Mensagem.OPCAO_INDISPONIVEL,
    Mensagem.CONVITE_PERGUNTA,
    Mensagem.PERGUNTA_VAZIA,
    Mensagem.SO_ENTENDO_TEXTO,
    MensagemAtendimento.DUVIDAS_PROVISORIA,
    MensagemAtendimento.CONTINGENCIA,
    MensagemAtendimento.SEM_CONDOMINIOS,
]


def _todas_as_identidades():
    return list(Mensagem) + list(MensagemAtendimento)


@pytest.mark.parametrize("identidade", _todas_as_identidades())
def test_toda_identidade_tem_redacao(identidade):
    """O inverso do teste do roteador: identidade sem texto é buraco de catálogo."""
    texto = renderizar(
        identidade, condominios=LISTA, nome_condominio="Edifício Alfa"
    )
    assert isinstance(texto, str) and texto.strip()


@pytest.mark.parametrize("identidade", _todas_as_identidades())
def test_nenhuma_promete_prazo(identidade):
    """Decisão de produto: 'indisponível' nunca vem com 'em breve', 'logo', data."""
    texto = renderizar(
        identidade, condominios=LISTA, nome_condominio="Edifício Alfa"
    ).lower()
    for proibido in ("em breve", "logo", "prazo", "aguarde", "próxim", "dia"):
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
