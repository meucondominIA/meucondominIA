"""Suíte de transições do wizard (Fase 4 · Etapa 3).

Toda transição sem rede, sem banco, sem IA e sem relógio — é o que o motor ser
puro compra. A lista de dias entra como valor FALSO, fabricado aqui: é o
congelamento do mapeamento no rascunho que torna isso possível.
"""

import inspect
import pathlib
import re
from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

import reserva
from areas import AreaReservavel
from reserva import (
    DIAS_POR_TELA,
    VER_MAIS,
    Concluir,
    Continuar,
    Encerrar,
    MensagemReserva,
    MostrarDias,
    Passo,
    RascunhoArea,
    RascunhoConfirmacao,
    RascunhoDia,
    avancar,
    gravar,
    ler,
    montar_pagina,
)
from roteador import Mensagem

SALAO = AreaReservavel(id=uuid4(), nome="Salão de Festas")
CHURRAS = AreaReservavel(id=uuid4(), nome="Churrasqueira")
UMA_AREA = [SALAO]
DUAS_AREAS = [SALAO, CHURRAS]

# Dias FALSOS: o passo `dia` nunca pergunta ao banco, então a lista é dado.
D = [date(2026, 8, dia) for dia in range(1, 21)]


def _dia_rascunho(*, opcoes=None, pagina=0, ultima=False):
    return RascunhoDia(
        area_id=SALAO.id,
        pagina=pagina,
        opcoes=D[:DIAS_POR_TELA] if opcoes is None else opcoes,
        ultima=ultima,
    )


CONFIRMACAO = RascunhoConfirmacao(area_id=SALAO.id, dia=D[0], pagina=0)


# ── entrada no wizard (rascunho is None) ─────────────────────────────────────


def test_entrada_abre_a_lista_de_areas():
    assert avancar(None, "2", areas=DUAS_AREAS) == Continuar(
        mensagem=MensagemReserva.ESCOLHER_AREA, rascunho=RascunhoArea()
    )


def test_entrada_sem_area_reservavel_nao_prende_no_wizard():
    assert avancar(None, "2", areas=[]) == Encerrar(mensagem=MensagemReserva.SEM_AREAS)


@pytest.mark.parametrize("escolha", ["2", "", "qualquer coisa", "0"])
def test_entrada_ignora_o_que_o_morador_digitou(escolha):
    """O "2" veio do menu; não escolhe nada DENTRO do wizard."""
    assert (
        avancar(None, escolha, areas=DUAS_AREAS).mensagem
        is MensagemReserva.ESCOLHER_AREA
    )


# ── passo area ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("escolha", ["1", "1.", " 1 ", "01", "1)"])
def test_area_aceita_o_que_o_morador_realmente_digita(escolha):
    assert avancar(RascunhoArea(), escolha, areas=DUAS_AREAS) == MostrarDias(
        area_id=SALAO.id, pagina=0
    )


def test_area_guarda_o_uuid_e_nao_o_numero():
    assert avancar(RascunhoArea(), "2", areas=DUAS_AREAS).area_id == CHURRAS.id


def test_area_indexa_a_lista_devolvida():
    """Trocar a ordem da lista troca o destino do mesmo número."""
    assert avancar(RascunhoArea(), "1", areas=[CHURRAS, SALAO]).area_id == CHURRAS.id


@pytest.mark.parametrize(
    "escolha", ["2", "9", "99", "0", "", "   ", "salão", "1 salão"]
)
def test_area_fora_da_faixa_ou_texto_livre_remostra(escolha):
    """Uma área só: tudo além do 1 é "não entendi", inclusive o escape."""
    assert avancar(RascunhoArea(), escolha, areas=UMA_AREA) == Continuar(
        mensagem=MensagemReserva.AREA_NAO_ENTENDIDA, rascunho=RascunhoArea()
    )


def test_area_escolhida_sempre_pede_leitura_de_dias():
    """A tela seguinte não existe sem dias_livres — daí o descritor."""
    assert isinstance(avancar(RascunhoArea(), "1", areas=UMA_AREA), MostrarDias)


# ── passo dia ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("n,esperado", [(1, D[0]), (4, D[3]), (7, D[6])])
def test_dia_resolve_pelo_mapeamento_congelado(n, esperado):
    assert avancar(_dia_rascunho(), str(n), areas=UMA_AREA) == Continuar(
        mensagem=MensagemReserva.CONFIRMAR,
        rascunho=RascunhoConfirmacao(area_id=SALAO.id, dia=esperado, pagina=0),
    )


def test_dia_nao_reparseia_data_nem_reconsulta():
    """O mesmo número resolve para datas diferentes se a TELA foi diferente."""
    outra = _dia_rascunho(opcoes=[date(2026, 9, 5), date(2026, 9, 12)])
    assert avancar(outra, "1", areas=UMA_AREA).rascunho.dia == date(2026, 9, 5)
    assert avancar(_dia_rascunho(), "1", areas=UMA_AREA).rascunho.dia == D[0]


def test_dia_preserva_a_pagina_para_a_volta():
    assert avancar(_dia_rascunho(pagina=3), "1", areas=UMA_AREA).rascunho.pagina == 3


def test_ver_mais_pede_a_proxima_pagina():
    assert avancar(_dia_rascunho(), str(VER_MAIS), areas=UMA_AREA) == MostrarDias(
        area_id=SALAO.id, pagina=1
    )


def test_ver_mais_na_ultima_pagina_responde_sem_tocar_o_banco():
    """`ultima` no rascunho evita ler para descobrir que não há mais nada."""
    rascunho = _dia_rascunho(pagina=1, ultima=True)
    avanco = avancar(rascunho, str(VER_MAIS), areas=UMA_AREA)
    assert avanco == Continuar(
        mensagem=MensagemReserva.ULTIMAS_DATAS, rascunho=rascunho
    )


@pytest.mark.parametrize(
    "escolha", ["8", "0", "10", "99", "", "  ", "25/07", "sábado"]
)
def test_dia_invalido_remostra_a_mesma_tela(escolha):
    """Re-numerar embaixo do morador é o bug que o congelamento evita."""
    rascunho = _dia_rascunho()
    assert avancar(rascunho, escolha, areas=UMA_AREA) == Continuar(
        mensagem=MensagemReserva.DIA_NAO_ENTENDIDO, rascunho=rascunho
    )


def test_dia_respeita_pagina_curta():
    """Página com 3 datas: o 4 já é inválido, mesmo cabendo em DIAS_POR_TELA."""
    rascunho = _dia_rascunho(opcoes=D[:3], ultima=True)
    assert avancar(rascunho, "3", areas=UMA_AREA).mensagem is MensagemReserva.CONFIRMAR
    assert (
        avancar(rascunho, "4", areas=UMA_AREA).mensagem
        is MensagemReserva.DIA_NAO_ENTENDIDO
    )


def test_ver_mais_nunca_colide_com_a_lista():
    assert DIAS_POR_TELA < VER_MAIS


# ── passo confirmacao ────────────────────────────────────────────────────────


def test_confirmacao_sim_vira_descritor_de_escrita():
    assert avancar(CONFIRMACAO, "1", areas=UMA_AREA) == Concluir(
        area_id=SALAO.id, dia=D[0]
    )


def test_confirmacao_nao_sai_sem_agendar():
    """Nada foi gravado até aqui: o 2 não cancela reserva, só não agenda."""
    assert avancar(CONFIRMACAO, "2", areas=UMA_AREA) == Encerrar(
        mensagem=Mensagem.NADA_AGENDADO
    )


@pytest.mark.parametrize("escolha", ["3", "0", "sim", "", "9", "confirmo"])
def test_confirmacao_qualquer_outra_coisa_repergunta(escolha):
    assert avancar(CONFIRMACAO, escolha, areas=UMA_AREA) == Continuar(
        mensagem=MensagemReserva.CONFIRMACAO_NAO_ENTENDIDA, rascunho=CONFIRMACAO
    )


def test_so_a_confirmacao_escreve():
    entradas = [
        (None, "2"),
        (RascunhoArea(), "1"),
        (_dia_rascunho(), "1"),
        (_dia_rascunho(), str(VER_MAIS)),
    ]
    for rascunho, escolha in entradas:
        assert not isinstance(avancar(rascunho, escolha, areas=DUAS_AREAS), Concluir)


# ── paginação ────────────────────────────────────────────────────────────────


def test_pagina_recorta_e_marca_que_ha_mais():
    pagina = montar_pagina(D, pagina=0)
    assert pagina.dias == D[:7] and pagina.pagina == 0 and pagina.ultima is False


def test_pagina_seguinte_continua_de_onde_parou():
    assert montar_pagina(D, pagina=1).dias == D[7:14]


def test_ultima_pagina_marca_ultima():
    pagina = montar_pagina(D, pagina=2)
    assert pagina.dias == D[14:20] and pagina.ultima is True


def test_multiplo_exato_nao_oferece_pagina_fantasma():
    """14 dias em telas de 7: a página 1 é a última, não existe página 2 vazia."""
    pagina = montar_pagina(D[:14], pagina=1)
    assert pagina.dias == D[7:14] and pagina.ultima is True


def test_pagina_alem_do_fim_cai_na_ultima_nao_vazia():
    """Os dias encolheram entre telas (outro morador reservou)."""
    pagina = montar_pagina(D[:10], pagina=5)
    assert pagina.dias == D[7:10] and pagina.pagina == 1 and pagina.ultima is True


def test_sem_dia_livre_nenhum_devolve_tela_vazia_explicita():
    pagina = montar_pagina([], pagina=0)
    assert pagina.dias == [] and pagina.ultima is True


def test_pagina_alimenta_o_rascunho_da_proxima_tela():
    """A costura: o recorte vira o mapeamento congelado, sem intermediário."""
    pagina = montar_pagina(D, pagina=1)
    rascunho = RascunhoDia(
        area_id=SALAO.id,
        pagina=pagina.pagina,
        opcoes=pagina.dias,
        ultima=pagina.ultima,
    )
    assert avancar(rascunho, "1", areas=UMA_AREA).rascunho.dia == D[7]


def test_janela_de_14_dias_cabe_em_duas_telas():
    """A decisão de produto (14 dias) casa com DIAS_POR_TELA sem página órfã."""
    from config import settings

    assert montar_pagina(D[: settings.reserva_janela_dias], pagina=1).ultima is True


# ── fronteira do jsonb ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "rascunho", [RascunhoArea(), _dia_rascunho(pagina=1, ultima=True), CONFIRMACAO]
)
def test_round_trip_pelo_jsonb(rascunho):
    import json

    bruto = gravar(rascunho)
    json.dumps(bruto)  # o codec do db.py é json.dumps: tem que aceitar
    assert ler(bruto) == rascunho


def test_passo_desconhecido_nao_vira_palpite():
    with pytest.raises(ValidationError):
        ler({"passo": "turno", "area_id": str(SALAO.id)})


def test_chave_a_mais_nao_passa():
    with pytest.raises(ValidationError):
        ler({**gravar(RascunhoArea()), "sobra": 1})


def test_todo_passo_tem_um_rascunho():
    """Passo sem modelo é buraco de catálogo."""
    modelos = {RascunhoArea, RascunhoDia, RascunhoConfirmacao}
    assert {m.model_fields["passo"].default for m in modelos} == set(Passo)


# ── invariantes estruturais ──────────────────────────────────────────────────


def test_motor_e_sincrono():
    """Se não é async, não consegue await — não toca I/O nem por acidente."""
    assert not inspect.iscoroutinefunction(avancar)
    assert not inspect.iscoroutinefunction(montar_pagina)


def test_motor_nao_importa_banco_nem_rede():
    fonte = pathlib.Path(reserva.__file__).read_text(encoding="utf-8")
    for proibido in ("asyncpg", "import db", "httpx", "openai"):
        assert proibido not in fonte, f"o motor não pode conhecer {proibido}"


def test_motor_nao_pergunta_que_dia_e_hoje():
    """`hoje` é do fuso do condomínio e vem da casca — senão a suíte precisaria
    congelar o relógio para ser determinística."""
    fonte = pathlib.Path(reserva.__file__).read_text(encoding="utf-8")
    assert not re.search(r"\b(today|now|utcnow)\s*\(", fonte)


def test_toda_entrada_produz_uma_decisao():
    """Nenhuma combinação devolve None — o buraco que um match sem `case _` abre."""
    rascunhos = [None, RascunhoArea(), _dia_rascunho(), CONFIRMACAO]
    escolhas = ["", "0", "1", "2", "9", "99", "abc", " 1 ", "1."]
    for rascunho in rascunhos:
        for escolha in escolhas:
            assert avancar(rascunho, escolha, areas=DUAS_AREAS) is not None
