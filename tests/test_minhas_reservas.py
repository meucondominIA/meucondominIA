"""O motor de "Minhas reservas" (função pura, sem I/O).

A âncora: o número N SEMPRE significa opcoes[N-1] do rascunho que gerou a tela.
Se essa propriedade cair, o morador cancela a reserva de outro dia.
"""

from datetime import date
from uuid import uuid4

import pytest

import minhas_reservas as mr
from reservas import ReservaDoMorador
from roteador import Mensagem


def _item(dia: date, area="Salão de Festas") -> ReservaDoMorador:
    return ReservaDoMorador(id=uuid4(), area=area, dia=dia)


DUAS = [_item(date(2026, 8, 12)), _item(date(2026, 8, 22))]
LISTA = mr.RascunhoLista(opcoes=DUAS)
CONFIRMACAO = mr.RascunhoConfirmacao(item=DUAS[0])


def test_entrada_no_fluxo_pede_a_leitura():
    """Sem rascunho é a entrada pelo menu; a escolha ('5') não escolhe nada."""
    assert mr.avancar(None, "5") == mr.MostrarLista()


@pytest.mark.parametrize("texto,indice", [("1", 0), ("2", 1), (" 2 ", 1), ("02.", 1)])
def test_numero_valido_leva_a_confirmacao_do_item_certo(texto, indice):
    avanco = mr.avancar(LISTA, texto)
    assert avanco == mr.Continuar(
        mensagem=mr.MensagemMinhasReservas.CONFIRMAR,
        rascunho=mr.RascunhoConfirmacao(item=DUAS[indice]),
    )


@pytest.mark.parametrize("texto", ["3", "0", "9", "42", "oi", "   ", ""])
def test_numero_invalido_remostra_a_MESMA_lista(texto):
    """Re-numerar embaixo do morador é o bug que o congelamento evita."""
    avanco = mr.avancar(LISTA, texto)
    assert avanco == mr.Continuar(
        mensagem=mr.MensagemMinhasReservas.RESERVA_NAO_ENTENDIDA, rascunho=LISTA
    )
    assert avanco.rascunho.opcoes == DUAS


def test_confirmar_com_1_vira_descritor_de_cancelamento():
    assert mr.avancar(CONFIRMACAO, "1") == mr.Cancelar(item=DUAS[0])


def test_confirmar_com_2_sai_sem_cancelar():
    assert mr.avancar(CONFIRMACAO, "2") == mr.Encerrar(
        mensagem=Mensagem.NADA_CANCELADO
    )


@pytest.mark.parametrize("texto", ["3", "0", "sim", "   "])
def test_confirmacao_nao_entendida_mantem_o_item(texto):
    avanco = mr.avancar(CONFIRMACAO, texto)
    assert avanco == mr.Continuar(
        mensagem=mr.MensagemMinhasReservas.CONFIRMACAO_NAO_ENTENDIDA,
        rascunho=CONFIRMACAO,
    )
    assert avanco.rascunho.item == DUAS[0]


def test_rascunho_faz_a_volta_pelo_jsonb_sem_perder_nada():
    """O rascunho vive como jsonb: UUID e date têm que voltar tipados."""
    for rascunho in (LISTA, CONFIRMACAO):
        assert mr.ler(mr.gravar(rascunho)) == rascunho


def test_gravar_produz_so_tipos_json():
    bruto = mr.gravar(LISTA)
    assert bruto["passo"] == "lista"
    assert all(isinstance(o["id"], str) and isinstance(o["dia"], str)
               for o in bruto["opcoes"])


@pytest.mark.parametrize(
    "bruto",
    [
        {"passo": "inexistente"},
        {"passo": "lista"},
        {"passo": "confirmacao"},
        {"passo": "lista", "opcoes": [], "extra": 1},
    ],
)
def test_rascunho_ilegivel_levanta_em_vez_de_virar_palpite(bruto):
    with pytest.raises(Exception):
        mr.ler(bruto)


def test_lista_vazia_nunca_aceita_numero():
    """A casca encerra antes com SEM_RESERVAS; se chegar aqui, nada é selecionável."""
    vazia = mr.RascunhoLista(opcoes=[])
    avanco = mr.avancar(vazia, "1")
    assert avanco.mensagem is mr.MensagemMinhasReservas.RESERVA_NAO_ENTENDIDA


def test_motor_nao_toca_banco_nem_rede():
    """Se não é async, não consegue await — a pureza vira propriedade da linguagem."""
    import inspect

    assert not inspect.iscoroutinefunction(mr.avancar)


def test_identidades_do_motor_nao_colidem_com_as_do_roteador():
    """`str` enums de valor igual são == e têm o mesmo hash: um valor repetido
    faria o braço errado do match capturar a mensagem."""
    assert not {m.value for m in mr.MensagemMinhasReservas} & {
        m.value for m in Mensagem
    }
