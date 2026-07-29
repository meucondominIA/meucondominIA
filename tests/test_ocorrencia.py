"""Testes do motor de ocorrência (Fase 4 · Etapa 4).

Toda transição, sem rede, sem banco, sem custo. Aqui isso é literal: `avancar`
não recebe NADA do banco, então não há fixture de dados a montar — a cobertura
completa sai de graça.
"""

import pathlib
import re

import pytest
from pydantic import ValidationError

from ocorrencia import (
    MAX_DESCRICAO,
    SEGUIR_SEM_FOTO,
    Anexo,
    Concluir,
    Continuar,
    Encerrar,
    Escolha,
    GuardarFoto,
    MensagemOcorrencia,
    Passo,
    RascunhoConfirmacao,
    RascunhoDescricao,
    RascunhoFoto,
    RascunhoTipo,
    TipoSolicitacao,
    avancar,
    gravar,
    ler,
)
from roteador import Estado, Mensagem

ANEXO = Anexo(
    bucket="anexos",
    caminho="cond/sol/c7a2.jpg",
    mimetype="image/jpeg",
    bytes=111582,
    sha256="c7a24f3c",
)
OUTRO_ANEXO = ANEXO.model_copy(update={"sha256": "deadbeef"})


def _texto(t: str) -> Escolha:
    return Escolha(texto=t)


def _foto(legenda: str | None = None) -> Escolha:
    """Como a foto chega na PRIMEIRA passada: marcador, sem anexo ainda."""
    return Escolha(texto=legenda, tem_foto=True, anexo=None)


def _subida(anexo: Anexo, legenda: str | None = None) -> Escolha:
    """A SEGUNDA passada: o upload já aconteceu, então não há mais o que subir."""
    return Escolha(texto=legenda, tem_foto=False, anexo=anexo)


# ── entrada no wizard ────────────────────────────────────────────────────────


def test_rascunho_nulo_abre_a_lista_de_tipos():
    avanco = avancar(None, _texto("3"))
    assert avanco == Continuar(
        mensagem=MensagemOcorrencia.ESCOLHER_TIPO, rascunho=RascunhoTipo()
    )


def test_entrada_ignora_a_escolha():
    """O "3" veio do menu e não escolhe nada aqui dentro."""
    for entrada in (_texto("3"), _texto("qualquer"), _foto()):
        assert avancar(None, entrada).mensagem is MensagemOcorrencia.ESCOLHER_TIPO


# ── passo TIPO ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "numero,esperado",
    [
        ("1", TipoSolicitacao.RECLAMACAO),
        ("2", TipoSolicitacao.OCORRENCIA),
        ("3", TipoSolicitacao.MANUTENCAO),
    ],
)
def test_cada_numero_mapeia_no_seu_tipo(numero, esperado):
    avanco = avancar(RascunhoTipo(), _texto(numero))
    assert avanco == Continuar(
        mensagem=MensagemOcorrencia.PEDIR_DESCRICAO,
        rascunho=RascunhoDescricao(tipo=esperado),
    )


@pytest.mark.parametrize("texto", ["0", "4", "9", "abc", "   ", "", "reclamação"])
def test_tipo_fora_da_faixa_reperguntam(texto):
    avanco = avancar(RascunhoTipo(), _texto(texto))
    assert avanco == Continuar(
        mensagem=MensagemOcorrencia.TIPO_NAO_ENTENDIDO, rascunho=RascunhoTipo()
    )


def test_foto_no_passo_tipo_nao_vira_anexo():
    """Sem saber o tipo não há rascunho onde pendurar a foto — subir arquivo que
    talvez não seja usado é lixo no Storage."""
    avanco = avancar(RascunhoTipo(), _foto("olha isso"))
    assert not isinstance(avanco, GuardarFoto)
    assert avanco.mensagem is MensagemOcorrencia.TIPO_NAO_ENTENDIDO


# ── passo DESCRIÇÃO ──────────────────────────────────────────────────────────

DESCRICAO = RascunhoDescricao(tipo=TipoSolicitacao.RECLAMACAO)


@pytest.mark.parametrize("texto", ["", "   ", "\n\n", "\t "])
def test_descricao_vazia_nao_avanca(texto):
    avanco = avancar(DESCRICAO, _texto(texto))
    assert avanco == Continuar(
        mensagem=MensagemOcorrencia.DESCRICAO_VAZIA, rascunho=DESCRICAO
    )


def test_descricao_no_limite_passa():
    avanco = avancar(DESCRICAO, _texto("a" * MAX_DESCRICAO))
    assert avanco.mensagem is MensagemOcorrencia.PEDIR_FOTO


def test_descricao_um_caractere_acima_do_limite_nao_passa():
    """O off-by-one explícito: MAX passa, MAX+1 não."""
    avanco = avancar(DESCRICAO, _texto("a" * (MAX_DESCRICAO + 1)))
    assert avanco == Continuar(
        mensagem=MensagemOcorrencia.DESCRICAO_LONGA, rascunho=DESCRICAO
    )


def test_descricao_de_texto_puro_pede_foto():
    avanco = avancar(DESCRICAO, _texto("Vazamento no 3º andar"))
    assert avanco == Continuar(
        mensagem=MensagemOcorrencia.PEDIR_FOTO,
        rascunho=RascunhoFoto(
            tipo=TipoSolicitacao.RECLAMACAO, descricao="Vazamento no 3º andar"
        ),
    )


def test_descricao_e_verbatim_no_miolo():
    """Emoji, quebras de linha, aspas e acento sobrevivem inteiros; só as pontas
    são aparadas."""
    bruto = "  Vazou 💧\nDesde ontem — 'sério'.\n\tE piora à noite  "
    avanco = avancar(DESCRICAO, _texto(bruto))
    assert avanco.rascunho.descricao == bruto.strip()
    assert "💧" in avanco.rascunho.descricao
    assert "\n\t" in avanco.rascunho.descricao


def test_nul_some_e_o_resto_fica():
    """O NUL não é escolha: `text` não o representa e o jsonb o rejeita."""
    avanco = avancar(DESCRICAO, _texto("antes\x00depois 💧"))
    assert avanco.rascunho.descricao == "antesdepois 💧"


def test_descricao_so_com_nul_conta_como_vazia():
    assert avancar(DESCRICAO, _texto("\x00\x00")).mensagem is (
        MensagemOcorrencia.DESCRICAO_VAZIA
    )


# ── a foto chegando junto da descrição ───────────────────────────────────────


def test_foto_na_descricao_pede_upload():
    assert avancar(DESCRICAO, _foto("vazamento")) == GuardarFoto(rascunho=DESCRICAO)


def test_foto_sem_legenda_guarda_o_anexo_e_ainda_pede_texto():
    """Ocorrência sem descrição não serve ao síndico."""
    avanco = avancar(DESCRICAO, _subida(ANEXO, legenda=None))
    assert avanco == Continuar(
        mensagem=MensagemOcorrencia.DESCRICAO_VAZIA,
        rascunho=RascunhoDescricao(tipo=TipoSolicitacao.RECLAMACAO, anexos=[ANEXO]),
    )


def test_foto_com_legenda_resolve_os_dois_e_pula_o_passo_da_foto():
    """Quem já mandou foto não precisa que a gente peça de novo."""
    avanco = avancar(DESCRICAO, _subida(ANEXO, legenda="Vazou na garagem"))
    assert avanco == Continuar(
        mensagem=MensagemOcorrencia.CONFIRMAR,
        rascunho=RascunhoConfirmacao(
            tipo=TipoSolicitacao.RECLAMACAO,
            descricao="Vazou na garagem",
            anexos=[ANEXO],
        ),
    )


def test_texto_depois_de_foto_sem_legenda_vai_direto_confirmar():
    """O anexo já está no rascunho, então não há foto a pedir."""
    com_anexo = RascunhoDescricao(tipo=TipoSolicitacao.MANUTENCAO, anexos=[ANEXO])
    avanco = avancar(com_anexo, _texto("Portão emperrado"))
    assert avanco == Continuar(
        mensagem=MensagemOcorrencia.CONFIRMAR,
        rascunho=RascunhoConfirmacao(
            tipo=TipoSolicitacao.MANUTENCAO,
            descricao="Portão emperrado",
            anexos=[ANEXO],
        ),
    )


def test_descricao_invalida_nao_perde_o_anexo_ja_guardado():
    com_anexo = RascunhoDescricao(tipo=TipoSolicitacao.RECLAMACAO, anexos=[ANEXO])
    avanco = avancar(com_anexo, _texto("   "))
    assert avanco.rascunho.anexos == [ANEXO]


# ── passo FOTO ───────────────────────────────────────────────────────────────

FOTO = RascunhoFoto(tipo=TipoSolicitacao.OCORRENCIA, descricao="Barulho no salão")


def test_foto_no_passo_foto_pede_upload():
    assert avancar(FOTO, _foto()) == GuardarFoto(rascunho=FOTO)


def test_anexo_subido_leva_a_confirmacao():
    avanco = avancar(FOTO, _subida(ANEXO))
    assert avanco == Continuar(
        mensagem=MensagemOcorrencia.CONFIRMAR,
        rascunho=RascunhoConfirmacao(
            tipo=TipoSolicitacao.OCORRENCIA,
            descricao="Barulho no salão",
            anexos=[ANEXO],
        ),
    )


def test_pular_a_foto_leva_a_confirmacao_sem_anexo():
    avanco = avancar(FOTO, _texto(str(SEGUIR_SEM_FOTO)))
    assert avanco.mensagem is MensagemOcorrencia.CONFIRMAR
    assert avanco.rascunho.anexos == []


@pytest.mark.parametrize("texto", ["2", "9", "abc", "   ", ""])
def test_texto_qualquer_no_passo_foto_repergunta(texto):
    avanco = avancar(FOTO, _texto(texto))
    assert avanco == Continuar(
        mensagem=MensagemOcorrencia.FOTO_NAO_ENTENDIDA, rascunho=FOTO
    )


def test_legenda_no_passo_foto_nao_sobrescreve_a_descricao():
    """A descrição já está fechada; trocá-la em silêncio apagaria o que o morador
    escreveu."""
    avanco = avancar(FOTO, _subida(ANEXO, legenda="outra coisa"))
    assert avanco.rascunho.descricao == "Barulho no salão"


# ── passo CONFIRMAÇÃO ────────────────────────────────────────────────────────

CONFIRMACAO = RascunhoConfirmacao(
    tipo=TipoSolicitacao.RECLAMACAO, descricao="Vazou", anexos=[ANEXO]
)


def test_sim_conclui_com_tudo_que_o_rascunho_carrega():
    assert avancar(CONFIRMACAO, _texto("1")) == Concluir(
        tipo=TipoSolicitacao.RECLAMACAO, descricao="Vazou", anexos=[ANEXO]
    )


def test_nao_encerra_sem_gravar():
    assert avancar(CONFIRMACAO, _texto("2")) == Encerrar(
        mensagem=Mensagem.NADA_REGISTRADO
    )


@pytest.mark.parametrize("texto", ["3", "9", "sim", "   ", ""])
def test_confirmacao_so_aceita_1_ou_2(texto):
    assert avancar(CONFIRMACAO, _texto(texto)) == Continuar(
        mensagem=MensagemOcorrencia.CONFIRMACAO_NAO_ENTENDIDA, rascunho=CONFIRMACAO
    )


def test_conclui_sem_anexo_quando_nao_houve_foto():
    sem_anexo = CONFIRMACAO.model_copy(update={"anexos": []})
    assert avancar(sem_anexo, _texto("1")).anexos == []


# ── serialização do rascunho ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "rascunho",
    [
        RascunhoTipo(),
        RascunhoDescricao(tipo=TipoSolicitacao.OCORRENCIA),
        RascunhoDescricao(tipo=TipoSolicitacao.MANUTENCAO, anexos=[ANEXO]),
        RascunhoFoto(tipo=TipoSolicitacao.RECLAMACAO, descricao="x"),
        RascunhoConfirmacao(
            tipo=TipoSolicitacao.RECLAMACAO,
            descricao="Vazou 💧",
            anexos=[ANEXO, OUTRO_ANEXO],
        ),
    ],
)
def test_rascunho_sobrevive_a_ida_e_volta(rascunho):
    assert ler(gravar(rascunho)) == rascunho


def test_gravar_produz_json_puro():
    """O codec do db.py é json.dumps: Enum e modelo aninhado precisam já ter
    virado tipo primitivo."""
    bruto = gravar(RascunhoConfirmacao(
        tipo=TipoSolicitacao.RECLAMACAO, descricao="x", anexos=[ANEXO]
    ))
    assert bruto["tipo"] == "reclamacao"
    assert isinstance(bruto["anexos"][0], dict)


@pytest.mark.parametrize(
    "bruto",
    [
        {"passo": "inexistente"},
        {},
        {"passo": "descricao"},
        {"passo": "descricao", "tipo": "sindico"},
        {"passo": "tipo", "sobrando": 1},
    ],
)
def test_rascunho_ilegivel_levanta(bruto):
    """Passo desconhecido levanta em vez de virar palpite — a casca reinicia."""
    with pytest.raises(ValidationError):
        ler(bruto)


def test_descricao_longa_demais_e_invariante_no_modelo():
    """No motor o limite vira tela; no modelo, exceção. São culpados diferentes."""
    with pytest.raises(ValidationError):
        RascunhoConfirmacao(
            tipo=TipoSolicitacao.RECLAMACAO, descricao="a" * (MAX_DESCRICAO + 1)
        )


# ── guardas de drift e de colisão ────────────────────────────────────────────


def test_tipos_do_python_batem_com_o_check_do_banco():
    """Guarda de drift: tipo novo no Python sem migration falha aqui.

    Varre TODAS as migrations em ordem e vale a ÚLTIMA definição — a lição da
    Etapa 3 foi apontar para a que está valendo, não para a primeira que casa.
    """
    # Ancorado em solicitacoes: `mensagens` também tem coluna `tipo`, e um regex
    # solto casa a errada (aconteceu ao escrever este teste).
    padrao = re.compile(
        r"(?:constraint\s+solicitacoes_tipo_check\s+|default\s+'ocorrencia'\s+)"
        r"check\s*\(\s*tipo in \(([^)]+)\)",
        re.IGNORECASE,
    )
    lista = None
    for arquivo in sorted(pathlib.Path("supabase/migrations").glob("*.sql")):
        for achado in padrao.finditer(arquivo.read_text(encoding="utf-8")):
            lista = achado.group(1)
    no_banco = set(re.findall(r"'([a-z_]+)'", lista))
    do_wizard = {tipo.value for tipo in TipoSolicitacao}
    assert do_wizard < no_banco
    assert no_banco - do_wizard == {"outro"}


def test_tipo_nao_colide_com_o_estado_de_mesmo_nome():
    """Se TipoSolicitacao virar `str, Enum`, ele fica == a Estado.OCORRENCIA, com
    o mesmo hash, e um match cai no braço errado. Medido em 28/07/2026."""
    assert TipoSolicitacao.OCORRENCIA != Estado.OCORRENCIA
    assert not isinstance(TipoSolicitacao.OCORRENCIA, str)
    assert {Estado.OCORRENCIA: "estado"}.get(TipoSolicitacao.OCORRENCIA) is None


def test_mensagens_do_motor_nao_colidem_com_as_do_roteador():
    valores = [m.value for m in MensagemOcorrencia] + [m.value for m in Mensagem]
    assert len(valores) == len(set(valores))


def test_todo_passo_tem_rascunho():
    """Passo sem rascunho seria estado inalcançável."""
    dos_rascunhos = {
        RascunhoTipo().passo,
        RascunhoDescricao(tipo=TipoSolicitacao.OCORRENCIA).passo,
        RascunhoFoto(tipo=TipoSolicitacao.OCORRENCIA, descricao="x").passo,
        RascunhoConfirmacao(tipo=TipoSolicitacao.OCORRENCIA, descricao="x").passo,
    }
    assert dos_rascunhos == set(Passo)
