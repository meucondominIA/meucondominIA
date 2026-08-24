"""Testes da CLI do QR de entrada (Etapa 7).

Molde do test_perguntar: fakes no namespace do módulo, porque aqui interessa a
ORQUESTRAÇÃO — o bootstrap abre e fecha sempre, o slug inativo sai limpo, e
sobretudo a ida e volta que decide se algum arquivo chega a existir.

O QR em si não é testado: quem garante a codificação é o segno.
"""

from pathlib import Path
from uuid import uuid4

import pytest

from condominios import FRASE_QR, CondominioElegivel
from ferramentas import qr

GABRO = CondominioElegivel(id=uuid4(), nome="Gabro")


@pytest.fixture
def harness(monkeypatch, tmp_path):
    eventos: list[str] = []
    estado = {"alvo": GABRO, "volta": GABRO}

    async def criar_pool():
        eventos.append("criar_pool")

    async def fechar_pool():
        eventos.append("fechar_pool")

    class _Acquire:
        async def __aenter__(self):
            return "conn-fake"

        async def __aexit__(self, *exc):
            return False

    class _Pool:
        def acquire(self):
            return _Acquire()

    async def buscar_elegivel_por_slug(conn, slug):
        return estado["alvo"] if slug == "res-gabro" else None

    async def resolver_por_frase(conn, texto):
        eventos.append(f"resolveu:{texto}")
        return estado["volta"]

    monkeypatch.setattr(qr, "criar_pool", criar_pool)
    monkeypatch.setattr(qr, "fechar_pool", fechar_pool)
    monkeypatch.setattr(qr, "get_pool", lambda: _Pool())
    monkeypatch.setattr(qr, "buscar_elegivel_por_slug", buscar_elegivel_por_slug)
    monkeypatch.setattr(qr, "resolver_por_frase", resolver_por_frase)
    monkeypatch.setattr(qr.settings, "whatsapp_numero", "555591919128")
    monkeypatch.chdir(tmp_path)

    return eventos, estado, tmp_path


def test_gera_o_arquivo_e_o_bootstrap_fecha(harness, capsys):
    eventos, _, tmp_path = harness
    qr.main(["--condominio", "res-gabro"])

    assert (tmp_path / "qr-res-gabro.svg").exists()
    assert eventos[0] == "criar_pool" and eventos[-1] == "fechar_pool"

    saida = capsys.readouterr().out
    assert f"{FRASE_QR}Gabro" in saida
    assert "https://wa.me/555591919128?text=" in saida


def test_a_frase_impressa_e_a_do_atendimento(harness):
    """Não há segunda redação: o que vai no papel é FRASE_QR + nome."""
    eventos, _, _ = harness
    qr.main(["--condominio", "res-gabro"])

    assert f"resolveu:{FRASE_QR}Gabro" in eventos


def test_ida_e_volta_divergente_nao_grava_arquivo(harness):
    """A guarda em tempo de execução: prefixo divergente ou nome ambíguo param
    aqui, antes de virar papel."""
    _, estado, tmp_path = harness
    estado["volta"] = None

    with pytest.raises(SystemExit) as saida:
        qr.main(["--condominio", "res-gabro"])

    assert "não resolve de volta" in str(saida.value)
    assert list(tmp_path.iterdir()) == []


def test_resolveu_outro_condominio_tambem_barra(harness, tmp_path):
    _, estado, _ = harness
    estado["volta"] = CondominioElegivel(id=uuid4(), nome="Gabro")

    with pytest.raises(SystemExit):
        qr.main(["--condominio", "res-gabro"])

    assert list(tmp_path.iterdir()) == []


def test_condominio_inativo_ou_inexistente_sai_limpo(harness):
    eventos, _, tmp_path = harness

    with pytest.raises(SystemExit) as saida:
        qr.main(["--condominio", "eval-sentinela"])

    assert "não encontrado ou inativo" in str(saida.value)
    assert eventos[-1] == "fechar_pool", "o pool fecha mesmo na falha"
    assert list(tmp_path.iterdir()) == []


def test_sem_numero_configurado_nao_abre_conexao(harness, monkeypatch):
    eventos, _, _ = harness
    monkeypatch.setattr(qr.settings, "whatsapp_numero", None)

    with pytest.raises(SystemExit) as saida:
        qr.main(["--condominio", "res-gabro"])

    assert "WHATSAPP_NUMERO" in str(saida.value)
    assert eventos == [], "falta de config não justifica abrir pool"


def test_slug_em_branco_e_erro_de_uso(harness):
    with pytest.raises(SystemExit) as saida:
        qr.main(["--condominio", "   "])
    assert saida.value.code == 2


def test_saida_e_formato_configuraveis(harness, tmp_path):
    qr.main(["--condominio", "res-gabro", "--formato", "png", "--saida",
             str(tmp_path / "mural.png")])
    assert (tmp_path / "mural.png").exists()


def test_link_url_encoda_o_acento():
    link = qr.montar_link("Olá! Sou morador do condomínio Gabro", "555591919128")
    assert link.startswith("https://wa.me/555591919128?text=")
    assert "%C3%A1" in link and "%C3%AD" in link
    assert " " not in link


def test_o_qr_nao_e_micro_e_usa_correcao_media(harness, tmp_path):
    """Default do segno seria L (7%) e Micro QR permitido — nenhum dos dois serve
    para papel lido de longe."""
    import segno

    qr.main(["--condominio", "res-gabro"])
    lido = segno.make(
        qr.montar_link(f"{FRASE_QR}Gabro", "555591919128"), error="M", micro=False
    )
    assert not lido.is_micro
    assert lido.error == "M"


def test_arquivo_gravado_e_svg_de_verdade(harness, tmp_path):
    qr.main(["--condominio", "res-gabro"])
    conteudo = Path(tmp_path / "qr-res-gabro.svg").read_text(encoding="utf-8")
    assert conteudo.lstrip().startswith("<?xml") or "<svg" in conteudo
