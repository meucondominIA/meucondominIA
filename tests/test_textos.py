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
from ocorrencia import Anexo, MensagemOcorrencia, TipoSolicitacao
from minhas_reservas import MensagemMinhasReservas
from reserva import MensagemReserva, montar_pagina
from reservas import ReservaDoMorador
from roteador import Mensagem, OpcaoMenu
from textos import MensagemAtendimento, MensagemSindico, renderizar

LISTA = [
    CondominioElegivel(id=uuid4(), nome="Edifício Alfa"),
    CondominioElegivel(id=uuid4(), nome="Edifício Beta"),
]
AREAS = [AreaReservavel(id=uuid4(), nome="Salão de Festas")]
PAGINA = montar_pagina([date(2026, 8, d) for d in range(1, 16)], pagina=0)

MINHAS = [
    ReservaDoMorador(id=uuid4(), area="Salão de Festas", dia=date(2026, 8, 12)),
    ReservaDoMorador(id=uuid4(), area="Salão de Festas", dia=date(2026, 8, 22)),
]

ANEXO = Anexo(
    bucket="anexos",
    caminho="cond/sol/abc.jpg",
    mimetype="image/jpeg",
    bytes=111582,
    sha256="abc",
)

_CONTEXTO = dict(
    condominios=LISTA,
    nome_condominio="Edifício Alfa",
    areas=AREAS,
    area="Salão de Festas",
    pagina=PAGINA,
    dia=date(2026, 8, 1),
    tipo=TipoSolicitacao.RECLAMACAO,
    descricao="Vazamento no 3º andar",
    anexos=[ANEXO],
    reservas=MINHAS,
)

_SEM_CONTEXTO = [
    Mensagem.MENU,
    Mensagem.MENU_NAO_ENTENDIDO,
    Mensagem.OPCAO_INDISPONIVEL,
    Mensagem.CONVITE_PERGUNTA,
    Mensagem.PERGUNTA_VAZIA,
    Mensagem.SO_ENTENDO_TEXTO,
    Mensagem.NADA_AGENDADO,
    Mensagem.NADA_CANCELADO,
    MensagemMinhasReservas.SEM_RESERVAS,
    MensagemMinhasReservas.JA_NAO_ATIVA,
    MensagemAtendimento.CONTINGENCIA,
    MensagemAtendimento.SEM_CONDOMINIOS,
    MensagemReserva.SEM_AREAS,
]

# DATA_TOMADA é PREFIXO de LISTA_DIAS, não tela: não renderiza sozinha.
_PREFIXO = {MensagemReserva.DATA_TOMADA}


def _todas_as_identidades():
    return [
        i
        for i in list(Mensagem)
        + list(MensagemAtendimento)
        + list(MensagemReserva)
        + list(MensagemOcorrencia)
        + list(MensagemMinhasReservas)
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
        i.value for i in list(Mensagem)
        + list(MensagemAtendimento)
        + list(MensagemReserva)
        + list(MensagemOcorrencia)
        + list(MensagemMinhasReservas)
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
    assert "5 - Minhas reservas" in texto
    assert "9 - Não sou desse condomínio" in texto
    # 6..8 seguem sem dono: não aparecem no menu
    for reservado in ("6 -", "7 -", "8 -"):
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


# ── avisos ao síndico (Fase 4 · Etapa 5) ─────────────────────────────────────

_ANEXO = Anexo(
    bucket="anexos", caminho="c/x.jpg", mimetype="image/jpeg", bytes=1, sha256="a"
)


def test_aviso_de_reserva_cita_tudo_que_o_sindico_precisa():
    texto = renderizar(
        MensagemSindico.AVISO_RESERVA,
        identificador="3f9a2b1c",
        area="Salão de Festas",
        dia=date(2026, 8, 8),
        telefone_morador="5555990000000",
    )
    assert texto.startswith("Nova reserva #3f9a2b1c")
    assert "Salão de Festas" in texto
    assert "sábado, 08/08" in texto
    assert "5555990000000" in texto


def test_aviso_de_cancelamento_diz_que_o_dia_voltou():
    texto = renderizar(
        MensagemSindico.AVISO_CANCELAMENTO,
        identificador="3f9a2b1c",
        area="Salão de Festas",
        dia=date(2026, 8, 8),
        telefone_morador="5555990000000",
    )
    assert texto.startswith("Reserva cancelada #3f9a2b1c")
    assert "sábado, 08/08" in texto
    assert "5555990000000" in texto
    assert texto.endswith("O dia voltou a ficar livre.")


def test_os_dois_avisos_da_mesma_reserva_se_distinguem_na_PRIMEIRA_linha():
    """A notificação do WhatsApp mostra o começo: se a diferença ficasse no
    rodapé, o síndico não saberia qual é qual sem abrir."""
    contexto = dict(
        identificador="3f9a2b1c",
        area="Salão de Festas",
        dia=date(2026, 8, 8),
        telefone_morador="5555990000000",
    )
    nova = renderizar(MensagemSindico.AVISO_RESERVA, **contexto)
    cancelada = renderizar(MensagemSindico.AVISO_CANCELAMENTO, **contexto)

    assert nova.splitlines()[0] != cancelada.splitlines()[0]
    assert "3f9a2b1c" in nova.splitlines()[0]
    assert "3f9a2b1c" in cancelada.splitlines()[0]


def test_nenhum_aviso_ao_sindico_promete_painel_ou_aprovacao():
    """Não há painel nem aprovação; prometer qualquer um dos dois é mentira."""
    contexto = dict(
        identificador="3f9a2b1c",
        area="Salão de Festas",
        dia=date(2026, 8, 8),
        telefone_morador="5555990000000",
    )
    for identidade in (
        MensagemSindico.AVISO_RESERVA,
        MensagemSindico.AVISO_CANCELAMENTO,
    ):
        texto = renderizar(identidade, **contexto).lower()
        assert "painel" not in texto and "aprova" not in texto


def test_confirmacao_ensina_o_caminho_do_cancelamento():
    """A reserva vira irreversível neste instante: a saída tem que estar à vista."""
    texto = renderizar(
        MensagemReserva.RESERVA_CONFIRMADA, area="Salão", dia=date(2026, 8, 8)
    )
    assert "confirmada" in texto
    assert f"{OpcaoMenu.MINHAS_RESERVAS.value} - Minhas reservas" in texto
    assert "pendente" not in texto.lower()


def test_lista_de_minhas_numera_a_partir_de_1_na_ordem_recebida():
    texto = renderizar(MensagemMinhasReservas.LISTA, reservas=MINHAS)
    assert "1 - Salão de Festas — qua 12/08" in texto
    assert "2 - Salão de Festas — sáb 22/08" in texto
    assert texto.index("1 - ") < texto.index("2 - ")
    assert "0 - Voltar ao menu" in texto


def test_tela_de_cancelar_recita_area_e_data_por_extenso():
    """O cinto de segurança: o morador confirma sobre o que está lendo."""
    texto = renderizar(
        MensagemMinhasReservas.CONFIRMAR, area="Salão de Festas", dia=date(2026, 8, 8)
    )
    assert "Cancelar esta reserva?" in texto
    assert "Salão de Festas" in texto and "sábado, 08/08" in texto
    assert "1 - Sim, cancelar" in texto and "2 - Não, voltar ao menu" in texto


def test_aviso_de_ocorrencia_reusa_o_resumo_do_morador():
    texto = renderizar(
        MensagemSindico.AVISO_OCORRENCIA,
        identificador="7b2e1a04",
        tipo=TipoSolicitacao.MANUTENCAO,
        descricao="Vazamento no 3º andar",
        anexos=[_ANEXO],
        telefone_morador="5555990000000",
    )
    assert texto.startswith("Ocorrência #7b2e1a04")
    assert "Manutenção" in texto
    assert "Vazamento no 3º andar" in texto
    assert "(1 foto anexada)" in texto
    assert "5555990000000" in texto


@pytest.mark.parametrize(
    "identidade, extra",
    [
        (
            MensagemSindico.AVISO_RESERVA,
            {"area": "Salão", "dia": date(2026, 8, 8)},
        ),
        (
            MensagemSindico.AVISO_OCORRENCIA,
            {"tipo": TipoSolicitacao.MANUTENCAO, "descricao": "x"},
        ),
    ],
)
def test_d5_o_aviso_nao_oferece_aprovacao_por_whatsapp(identidade, extra):
    """A aprovação é no portal (D5 = Plano 1). Regressão de produto vira teste."""
    texto = renderizar(
        identidade, identificador="abc12345", telefone_morador="5511", **extra
    )
    for proibido in ("Aprovar", "Recusar", "Responda com o número", "1 - "):
        assert proibido not in texto


@pytest.mark.parametrize(
    "identidade, extra",
    [
        (
            MensagemSindico.AVISO_RESERVA,
            {"area": "Salão", "dia": date(2026, 8, 8)},
        ),
        (
            MensagemSindico.AVISO_OCORRENCIA,
            {"tipo": TipoSolicitacao.MANUTENCAO, "descricao": "x"},
        ),
    ],
)
def test_aviso_sem_identificador_levanta(identidade, extra):
    """Sem identificador o síndico não consegue casar o aviso com o pedido."""
    with pytest.raises(ValueError, match="identificador"):
        renderizar(identidade, telefone_morador="5511", **extra)


def test_texto_da_borda_nao_promete_portal():
    """O painel só existe na Fase 5 — prometer aqui seria mentira ao síndico."""
    texto = renderizar(MensagemSindico.SEM_CANAL)
    assert "síndico" in texto
    assert "painel" not in texto.lower()


@pytest.mark.parametrize(
    "identidade, extra",
    [
        (MensagemReserva.RESERVA_CONFIRMADA, {"area": "Salão", "dia": date(2026, 8, 8)}),
        (
            MensagemOcorrencia.REGISTRADA,
            {"tipo": TipoSolicitacao.MANUTENCAO, "descricao": "vazou"},
        ),
    ],
)
def test_lgpd_o_morador_e_avisado_de_que_o_numero_vai_ao_sindico(identidade, extra):
    """Base legal é procedimento a pedido do titular: exige transparência."""
    texto = renderizar(identidade, **extra)
    assert "O síndico recebe seu número e est" in texto
