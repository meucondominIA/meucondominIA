"""Testes da máquina de estados (Fase 3 · Passo 2).

Toda transição, sem rede, sem banco, sem custo — é o que o roteador ser puro
compra. Os casos "chatos" (texto livre, número fora da faixa, escape, áudio,
só espaços) valem tanto quanto os felizes: são eles que o morador real produz.
"""

import inspect
import pathlib
import re
from uuid import uuid4

import pytest

from roteador import (
    Conversa,
    DelegarDuvida,
    DelegarIdentificacao,
    Estado,
    Mensagem,
    Responder,
    Transicao,
    rotear,
)
from zpro_models import MessageType

CONDOMINIO = uuid4()
CANDIDATO = uuid4()


def _conversa(estado: Estado, *, condominio=None, pendente=None) -> Conversa:
    return Conversa(
        id=uuid4(),
        estado=estado,
        condominio_id=condominio,
        condominio_pendente=pendente,
    )


def _texto(conversa: Conversa, texto: str):
    return rotear(conversa, tipo=MessageType.TEXT, texto=texto)


def _audio(conversa: Conversa):
    return rotear(conversa, tipo=MessageType.UNSUPPORTED, texto=None)


# ── identificacao ────────────────────────────────────────────────────────────

IDENTIFICACAO = _conversa(Estado.IDENTIFICACAO)


@pytest.mark.parametrize("texto,indice", [("1", 1), ("2.", 2), (" 3 ", 3), ("04", 4)])
def test_identificacao_numero_delega_ao_passo_3(texto, indice):
    """O roteador não sabe quantos condomínios existem — quem resolve é a lista."""
    assert _texto(IDENTIFICACAO, texto) == DelegarIdentificacao(indice=indice)


@pytest.mark.parametrize("texto", ["oi", "moro no gabro", "   ", "um", "1 gabro"])
def test_identificacao_texto_livre_pede_numero(texto):
    assert _texto(IDENTIFICACAO, texto) == Responder(
        mensagem=Mensagem.CONDOMINIO_NAO_ENTENDIDO
    )


def test_identificacao_escape_nao_e_atalho():
    """Não existe menu para voltar antes de a pessoa ser identificada."""
    assert _texto(IDENTIFICACAO, "0") == Responder(
        mensagem=Mensagem.CONDOMINIO_NAO_ENTENDIDO
    )


def test_identificacao_audio():
    assert _audio(IDENTIFICACAO) == Responder(mensagem=Mensagem.SO_ENTENDO_TEXTO)


# ── aguardando_confirmacao ───────────────────────────────────────────────────

CONFIRMACAO = _conversa(Estado.AGUARDANDO_CONFIRMACAO, pendente=CANDIDATO)


def test_confirmacao_sim_promove_o_candidato():
    assert _texto(CONFIRMACAO, "1") == Responder(
        mensagem=Mensagem.MENU, transicao=Transicao.para_menu(CANDIDATO)
    )


def test_confirmacao_nao_volta_a_lista():
    assert _texto(CONFIRMACAO, "2") == Responder(
        mensagem=Mensagem.PEDIR_CONDOMINIO, transicao=Transicao.para_identificacao()
    )


@pytest.mark.parametrize("texto", ["3", "0", "sim", "isso mesmo", "   "])
def test_confirmacao_qualquer_outra_coisa_repergunta(texto):
    assert _texto(CONFIRMACAO, texto) == Responder(
        mensagem=Mensagem.CONFIRMACAO_NAO_ENTENDIDA
    )


def test_confirmacao_audio():
    assert _audio(CONFIRMACAO) == Responder(mensagem=Mensagem.SO_ENTENDO_TEXTO)


def test_confirmacao_sem_candidato_recomeca_a_identificacao():
    """Cenário real do on delete set null: o candidato sumiu. Sem isto, laço morto."""
    orfa = _conversa(Estado.AGUARDANDO_CONFIRMACAO)
    esperado = Responder(
        mensagem=Mensagem.PEDIR_CONDOMINIO, transicao=Transicao.para_identificacao()
    )
    assert _texto(orfa, "1") == esperado
    assert _texto(orfa, "qualquer coisa") == esperado
    assert _audio(orfa) == esperado  # a recuperação vem ANTES do filtro de tipo


# ── menu ─────────────────────────────────────────────────────────────────────

MENU = _conversa(Estado.MENU, condominio=CONDOMINIO)


def test_menu_opcao_1_abre_duvidas():
    assert _texto(MENU, "1") == Responder(
        mensagem=Mensagem.CONVITE_PERGUNTA,
        transicao=Transicao.para_duvidas(CONDOMINIO),
    )


@pytest.mark.parametrize("texto", ["2", "3", "4"])
def test_menu_opcoes_nao_implementadas(texto):
    assert _texto(MENU, texto) == Responder(mensagem=Mensagem.OPCAO_INDISPONIVEL)


def test_menu_trocar_condominio_zera_o_tenant():
    decisao = _texto(MENU, "5")
    assert decisao == Responder(
        mensagem=Mensagem.PEDIR_CONDOMINIO, transicao=Transicao.para_identificacao()
    )
    assert decisao.transicao.condominio_id is None  # não pode sobrar o antigo


@pytest.mark.parametrize("texto", ["6", "9", "42"])
def test_menu_numero_fora_da_faixa(texto):
    assert _texto(MENU, texto) == Responder(mensagem=Mensagem.MENU_NAO_ENTENDIDO)


@pytest.mark.parametrize("texto", ["quero falar com o sindico", "oi", "   "])
def test_menu_texto_livre_reapresenta_sem_soar_erro(texto):
    assert _texto(MENU, texto) == Responder(mensagem=Mensagem.MENU)


def test_menu_escape_e_inofensivo():
    assert _texto(MENU, "0") == Responder(mensagem=Mensagem.MENU)


def test_menu_audio():
    assert _audio(MENU) == Responder(mensagem=Mensagem.SO_ENTENDO_TEXTO)


# ── duvidas ──────────────────────────────────────────────────────────────────

DUVIDAS = _conversa(Estado.DUVIDAS, condominio=CONDOMINIO)


def test_duvidas_escape_volta_ao_menu():
    assert _texto(DUVIDAS, "0") == Responder(
        mensagem=Mensagem.MENU, transicao=Transicao.para_menu(CONDOMINIO)
    )


@pytest.mark.parametrize(
    "texto", ["Posso ter cachorro?", "45", "Art. 45", "e para a churrasqueira?"]
)
def test_duvidas_tudo_que_nao_e_escape_e_pergunta(texto):
    assert _texto(DUVIDAS, texto) == DelegarDuvida(pergunta=texto)


def test_duvidas_pergunta_chega_sem_espaco_sobrando():
    assert _texto(DUVIDAS, "  posso ter gato?  ") == DelegarDuvida(
        pergunta="posso ter gato?"
    )


def test_duvidas_so_espacos():
    assert _texto(DUVIDAS, "   ") == Responder(mensagem=Mensagem.PERGUNTA_VAZIA)


def test_duvidas_audio():
    assert _audio(DUVIDAS) == Responder(mensagem=Mensagem.SO_ENTENDO_TEXTO)


def test_duvidas_nao_muda_de_estado_ao_perguntar():
    assert isinstance(_texto(DUVIDAS, "Posso ter cachorro?"), DelegarDuvida)


# ── invariantes do passo ─────────────────────────────────────────────────────


def test_rotear_e_sincrona():
    """Se não é async, não consegue await — não toca I/O nem por acidente."""
    assert not inspect.iscoroutinefunction(rotear)


def test_estados_do_python_batem_com_o_check_do_banco():
    """Guarda de drift: acrescentar estado no Python sem migration falha aqui."""
    sql = next(
        pathlib.Path("supabase/migrations").glob("*_conversas_estado.sql")
    ).read_text(encoding="utf-8")
    lista = re.search(r"estado in \(([^)]+)\)", sql, re.IGNORECASE).group(1)
    no_banco = set(re.findall(r"'([a-z_]+)'", lista))
    assert no_banco == {estado.value for estado in Estado}


@pytest.mark.parametrize(
    "transicao",
    [
        Transicao.para_identificacao(),
        Transicao.para_confirmacao(CANDIDATO),
        Transicao.para_menu(CONDOMINIO),
        Transicao.para_duvidas(CONDOMINIO),
    ],
)
def test_construtores_produzem_trinca_coerente(transicao):
    """Mesma regra do chk_conversas_estado_coerente, em forma de teste."""
    match transicao.estado:
        case Estado.IDENTIFICACAO:
            assert transicao.condominio_id is None
            assert transicao.condominio_pendente is None
        case Estado.AGUARDANDO_CONFIRMACAO:
            assert transicao.condominio_id is None
        case Estado.MENU | Estado.DUVIDAS:
            assert transicao.condominio_id is not None
            assert transicao.condominio_pendente is None


def test_menu_sem_condominio_falha_alto():
    """Impossível pelo CHECK: se acontecer, o dado foi adulterado. Não adivinha."""
    with pytest.raises(ValueError, match="chk_conversas_estado_coerente"):
        _texto(_conversa(Estado.MENU), "1")


def test_toda_mensagem_declarada_e_alcancavel():
    """Identidade que o roteador não emite é campo morto. Se o passo 3 precisar
    de identidades próprias (a confirmação, por exemplo), elas nascem lá."""
    conversas = [
        IDENTIFICACAO,
        CONFIRMACAO,
        _conversa(Estado.AGUARDANDO_CONFIRMACAO),
        MENU,
        DUVIDAS,
    ]
    emitidas = set()
    for conversa in conversas:
        emitidas.add(_audio(conversa).mensagem)
        for texto in ("1", "2", "5", "0", "9", "oi", "   "):
            decisao = _texto(conversa, texto)
            if isinstance(decisao, Responder):
                emitidas.add(decisao.mensagem)

    assert emitidas == set(Mensagem)


def test_decisao_carrega_no_maximo_uma_mensagem():
    """uq_mensagens_em_resposta_a é único: uma resposta por mensagem recebida."""
    decisao = _texto(MENU, "1")
    assert isinstance(decisao.mensagem, Mensagem)
