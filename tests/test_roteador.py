"""Testes da máquina de estados (Fase 3 · Passo 2).

Toda transição, sem rede, sem banco, sem custo — é o que o roteador ser puro
compra. Os casos "chatos" (texto livre, número fora da faixa, escape, áudio,
só espaços) valem tanto quanto os felizes: são eles que o morador real produz.
"""

import inspect
import pathlib
import re
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from roteador import (
    Conversa,
    DelegarDuvida,
    DelegarIdentificacao,
    DelegarMinhasReservas,
    DelegarOcorrencia,
    DelegarReserva,
    Estado,
    Mensagem,
    Responder,
    Transicao,
    rotear,
)
from zpro_models import MessageType

CONDOMINIO = uuid4()
CANDIDATO = uuid4()
_AGORA = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


def _conversa(
    estado: Estado, *, condominio=None, pendente=None, rascunho=None
) -> Conversa:
    return Conversa(
        id=uuid4(),
        estado=estado,
        condominio_id=condominio,
        condominio_pendente=pendente,
        ultima_interacao_em=_AGORA,
        telefone="5555999999999",
        rascunho=rascunho,
    )


def _texto(conversa: Conversa, texto: str, *, precisa_reconfirmar: bool = False):
    return rotear(
        conversa,
        tipo=MessageType.TEXT,
        texto=texto,
        precisa_reconfirmar=precisa_reconfirmar,
    )


def _foto(conversa: Conversa, texto: str | None = None):
    """Foto do Z-PRO: `texto` é a legenda, e ela pode não existir."""
    return rotear(conversa, tipo=MessageType.IMAGE, texto=texto)


def _audio(conversa: Conversa, *, precisa_reconfirmar: bool = False):
    return rotear(
        conversa,
        tipo=MessageType.UNSUPPORTED,
        texto=None,
        precisa_reconfirmar=precisa_reconfirmar,
    )


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


@pytest.mark.parametrize("texto", ["4"])
def test_menu_opcoes_nao_implementadas(texto):
    assert _texto(MENU, texto) == Responder(mensagem=Mensagem.OPCAO_INDISPONIVEL)


def test_menu_opcao_2_abre_a_reserva():
    """Deixou de ser muro: o ramo 2 delega ao motor, como o 1 delega à geração."""
    assert _texto(MENU, "2") == DelegarReserva(escolha="2")


def test_menu_opcao_3_abre_a_ocorrencia():
    """O ramo 3 deixou de responder 'indisponível' e passou a delegar."""
    assert _texto(MENU, "3") == DelegarOcorrencia(texto="3", tem_foto=False)


def test_menu_trocar_condominio_zera_o_tenant():
    decisao = _texto(MENU, "9")
    assert decisao == Responder(
        mensagem=Mensagem.PEDIR_CONDOMINIO, transicao=Transicao.para_identificacao()
    )
    assert decisao.transicao.condominio_id is None  # não pode sobrar o antigo


@pytest.mark.parametrize("texto", ["6", "7", "8", "42"])
def test_menu_numero_reservado_ou_fora_da_faixa(texto):
    """6..8 seguem reservados sem dono; 42 é fora de tudo. Todos: não entendido."""
    assert _texto(MENU, texto) == Responder(mensagem=Mensagem.MENU_NAO_ENTENDIDO)


def test_menu_opcao_5_abre_minhas_reservas():
    assert _texto(MENU, "5") == DelegarMinhasReservas(escolha="5")


@pytest.mark.parametrize("texto", ["quero falar com o sindico", "oi", "   "])
def test_menu_texto_livre_reapresenta_sem_soar_erro(texto):
    assert _texto(MENU, texto) == Responder(mensagem=Mensagem.MENU)


def test_menu_escape_e_inofensivo():
    assert _texto(MENU, "0") == Responder(mensagem=Mensagem.MENU)


def test_menu_audio():
    assert _audio(MENU) == Responder(mensagem=Mensagem.SO_ENTENDO_TEXTO)


# ── reconfirmação de sessão ──────────────────────────────────────────────────


@pytest.mark.parametrize("estado", [Estado.MENU, Estado.DUVIDAS])
def test_sessao_expirada_devolve_o_tenant_ao_limbo(estado):
    """Sessão nova em menu/duvidas: o condomínio confirmado volta a candidato."""
    conversa = _conversa(estado, condominio=CONDOMINIO)
    assert _texto(conversa, "1", precisa_reconfirmar=True) == Responder(
        mensagem=Mensagem.RECONFIRMAR_CONDOMINIO,
        transicao=Transicao.para_confirmacao(CONDOMINIO),
    )


def test_reconfirmacao_nao_dispara_antes_do_menu():
    """Em identificacao/confirmacao não há tenant a reconfirmar: segue o fluxo."""
    assert isinstance(_texto(IDENTIFICACAO, "1", precisa_reconfirmar=True), DelegarIdentificacao)
    assert _texto(CONFIRMACAO, "1", precisa_reconfirmar=True).mensagem is Mensagem.MENU


def test_reconfirmacao_cede_ao_filtro_de_tipo():
    """Áudio com sessão expirada ouve 'só texto', não roda em círculo pedindo 1/2."""
    assert _audio(MENU, precisa_reconfirmar=True) == Responder(
        mensagem=Mensagem.SO_ENTENDO_TEXTO
    )


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


# ── reserva ──────────────────────────────────────────────────────────────────

RESERVA = _conversa(
    Estado.RESERVA, condominio=CONDOMINIO, rascunho={"passo": "area"}
)


def test_reserva_escape_sai_sem_agendar():
    """O 0 é resolvido no roteador: funciona mesmo com rascunho ilegível.
    para_menu limpa o rascunho por construção — o CHECK exige."""
    decisao = _texto(RESERVA, "0")
    assert decisao == Responder(
        mensagem=Mensagem.NADA_AGENDADO, transicao=Transicao.para_menu(CONDOMINIO)
    )
    assert decisao.transicao.rascunho is None


@pytest.mark.parametrize("texto", ["1", "9", "99", "abc", "   ", "25/07"])
def test_reserva_tudo_que_nao_e_escape_delega(texto):
    """O roteador trata `reserva` como opaca: quem lê o passo é o motor."""
    assert _texto(RESERVA, texto) == DelegarReserva(escolha=texto)


def test_reserva_nao_olha_o_passo():
    """O mesmo texto delega igual, esteja o rascunho em qualquer passo."""
    for passo in ("area", "dia", "confirmacao"):
        conversa = _conversa(
            Estado.RESERVA, condominio=CONDOMINIO, rascunho={"passo": passo}
        )
        assert _texto(conversa, "1") == DelegarReserva(escolha="1")


def test_reserva_com_sessao_expirada_reconfirma_antes_de_gravar():
    """Reserva é ESCRITA: gravar no tenant não reconfirmado é pior que responder
    dúvida errada. O rascunho abandonado some — nada foi gravado."""
    decisao = _texto(RESERVA, "1", precisa_reconfirmar=True)
    assert decisao == Responder(
        mensagem=Mensagem.RECONFIRMAR_CONDOMINIO,
        transicao=Transicao.para_confirmacao(CONDOMINIO),
    )
    assert decisao.transicao.rascunho is None


def test_reserva_audio():
    assert _audio(RESERVA) == Responder(mensagem=Mensagem.SO_ENTENDO_TEXTO)


# ── ocorrência (Fase 4 · Etapa 4) ────────────────────────────────────────────

OCORRENCIA = _conversa(
    Estado.OCORRENCIA, condominio=CONDOMINIO, rascunho={"passo": "tipo"}
)

MINHAS_RESERVAS = _conversa(
    Estado.MINHAS_RESERVAS, condominio=CONDOMINIO, rascunho={"passo": "lista"}
)


def test_ocorrencia_escape_volta_ao_menu_sem_registrar():
    decisao = _texto(OCORRENCIA, "0")
    assert decisao == Responder(
        mensagem=Mensagem.NADA_REGISTRADO, transicao=Transicao.para_menu(CONDOMINIO)
    )
    assert decisao.transicao.rascunho is None


@pytest.mark.parametrize("texto", ["1", "9", "99", "abc", "   ", "vazou tudo"])
def test_ocorrencia_tudo_que_nao_e_escape_delega(texto):
    """O roteador trata `ocorrencia` como opaca: quem lê o passo é o motor."""
    assert _texto(OCORRENCIA, texto) == DelegarOcorrencia(texto=texto, tem_foto=False)


def test_ocorrencia_nao_olha_o_passo():
    for passo in ("tipo", "descricao", "foto", "confirmacao"):
        conversa = _conversa(
            Estado.OCORRENCIA, condominio=CONDOMINIO, rascunho={"passo": passo}
        )
        assert _texto(conversa, "1") == DelegarOcorrencia(texto="1", tem_foto=False)


def test_ocorrencia_aceita_foto_sem_legenda():
    """A guarda global de tipo cede AQUI, e só aqui: foto sem texto é entrada
    legítima, não 'só entendo texto'."""
    assert _foto(OCORRENCIA) == DelegarOcorrencia(texto=None, tem_foto=True)


def test_ocorrencia_aceita_foto_com_legenda():
    assert _foto(OCORRENCIA, "vazamento na garagem") == DelegarOcorrencia(
        texto="vazamento na garagem", tem_foto=True
    )


def test_legenda_zero_nao_e_escape():
    """Escape é comando digitado. Legenda "0" numa foto não pode tirar o morador
    do wizard — ele mandou uma foto, não pediu para sair."""
    assert _foto(OCORRENCIA, "0") == DelegarOcorrencia(texto="0", tem_foto=True)


@pytest.mark.parametrize("conversa", [MENU, DUVIDAS, RESERVA, IDENTIFICACAO])
def test_foto_fora_da_ocorrencia_segue_barrada(conversa):
    """_ACEITAM_IMAGEM é uma lista de um: alargar a guarda não pode ter alargado
    para todo mundo."""
    assert _foto(conversa, "legenda") == Responder(
        mensagem=Mensagem.SO_ENTENDO_TEXTO
    )


def test_ocorrencia_audio_segue_barrado():
    """Aceitar imagem não é aceitar mídia: áudio continua fora."""
    assert _audio(OCORRENCIA) == Responder(mensagem=Mensagem.SO_ENTENDO_TEXTO)


def test_ocorrencia_com_sessao_expirada_reconfirma_antes_de_gravar():
    """Ocorrência é ESCRITA, como a reserva: gravar no tenant não reconfirmado
    abre a solicitação no condomínio errado."""
    decisao = _texto(OCORRENCIA, "1", precisa_reconfirmar=True)
    assert decisao == Responder(
        mensagem=Mensagem.RECONFIRMAR_CONDOMINIO,
        transicao=Transicao.para_confirmacao(CONDOMINIO),
    )
    assert decisao.transicao.rascunho is None


def test_foto_com_sessao_expirada_reconfirma():
    """A reconfirmação vem DEPOIS do filtro de tipo: a foto atravessa o filtro e
    é reconfirmada, em vez de virar 'só entendo texto'."""
    decisao = rotear(
        OCORRENCIA, tipo=MessageType.IMAGE, texto=None, precisa_reconfirmar=True
    )
    assert decisao.mensagem is Mensagem.RECONFIRMAR_CONDOMINIO


def test_duvidas_nao_muda_de_estado_ao_perguntar():
    assert isinstance(_texto(DUVIDAS, "Posso ter cachorro?"), DelegarDuvida)


# ── invariantes do passo ─────────────────────────────────────────────────────


def test_rotear_e_sincrona():
    """Se não é async, não consegue await — não toca I/O nem por acidente."""
    assert not inspect.iscoroutinefunction(rotear)


def test_estados_do_python_batem_com_o_check_do_banco():
    """Guarda de drift: acrescentar estado no Python sem migration falha aqui."""
    # Varre TODAS as migrations em ordem: o CHECK é dropado e recriado, então
    # vale a ÚLTIMA recriação. O \s+ evita casar chk_conversas_estado_coerente,
    # cujo `estado in (...)` lista só um subconjunto.
    padrao = re.compile(
        r"add constraint chk_conversas_estado\s+check\s*\(\s*estado in \(([^)]+)\)",
        re.IGNORECASE,
    )
    lista = None
    for arquivo in sorted(pathlib.Path("supabase/migrations").glob("*.sql")):
        for achado in padrao.finditer(arquivo.read_text(encoding="utf-8")):
            lista = achado.group(1)
    no_banco = set(re.findall(r"'([a-z_]+)'", lista))
    assert no_banco == {estado.value for estado in Estado}


@pytest.mark.parametrize(
    "transicao",
    [
        Transicao.para_identificacao(),
        Transicao.para_confirmacao(CANDIDATO),
        Transicao.para_menu(CONDOMINIO),
        Transicao.para_duvidas(CONDOMINIO),
        Transicao.para_reserva(CONDOMINIO, {"passo": "area"}),
        Transicao.para_ocorrencia(CONDOMINIO, {"passo": "tipo"}),
        Transicao.para_minhas_reservas(CONDOMINIO, {"passo": "lista"}),
    ],
)
def test_construtores_produzem_destino_coerente(transicao):
    """Mesma regra do chk_conversas_estado_coerente + chk_conversas_rascunho,
    em forma de teste."""
    match transicao.estado:
        case Estado.IDENTIFICACAO:
            assert transicao.condominio_id is None
            assert transicao.condominio_pendente is None
        case Estado.AGUARDANDO_CONFIRMACAO:
            assert transicao.condominio_id is None
        case (
            Estado.MENU
            | Estado.DUVIDAS
            | Estado.RESERVA
            | Estado.OCORRENCIA
            | Estado.MINHAS_RESERVAS
        ):
            assert transicao.condominio_id is not None
            assert transicao.condominio_pendente is None

    if transicao.estado in (
        Estado.RESERVA, Estado.OCORRENCIA, Estado.MINHAS_RESERVAS
    ):
        assert isinstance(transicao.rascunho, dict)
    else:
        assert transicao.rascunho is None


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
        RESERVA,
        OCORRENCIA,
        MINHAS_RESERVAS,
    ]
    emitidas = set()
    for conversa in conversas:
        emitidas.add(_audio(conversa).mensagem)
        for reconfirmar in (False, True):
            for texto in ("1", "2", "3", "4", "5", "6", "0", "9", "oi", "   "):
                decisao = _texto(conversa, texto, precisa_reconfirmar=reconfirmar)
                if isinstance(decisao, Responder):
                    emitidas.add(decisao.mensagem)

    assert emitidas == set(Mensagem)


def test_decisao_carrega_no_maximo_uma_mensagem():
    """uq_mensagens_em_resposta_a é único: uma resposta por mensagem recebida."""
    decisao = _texto(MENU, "1")
    assert isinstance(decisao.mensagem, Mensagem)
