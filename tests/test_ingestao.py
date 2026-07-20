"""Testes da CLI de ingestão (Fase 2 · Passo 5).

Sem rede e sem banco: fakes registram a ORDEM dos eventos, porque a regra de
ouro do projeto é sequencial — slug resolvido com conexão curta já devolvida,
rede sem nenhuma conn adquirida, e a conn da escrita só depois dos vetores,
dentro de uma transação única. O teste da ordem é o teste da arquitetura.

O dry-run é provado hostil: bootstrap/rede/banco viram bombas que explodem se
tocadas. O contrato de saída segue a doc: sys.exit(str) → mensagem no stderr
e código 1; erro de uso do argparse → código 2.
"""

import pytest

import embeddings
from ferramentas import ingestao

DOC = "Regimento Exemplo"

OCR = """\
===== Página 1 =====

Art. 1º - Primeira regra do condomínio exemplo.

[[CARTÓRIO: rubrica no rodapé]]
===== Página 2 =====

DAS PENALIDADES

Art. 2º - Multa simples por infração.

===== Página 3 =====

[Página composta por blocos de formulário de assinatura dos condôminos:]
Nome: [manuscrito: FULANA SIGILOSA]
"""

ID_CONDOMINIO = "11111111-1111-1111-1111-111111111111"


class _FakeTx:
    def __init__(self, eventos):
        self._eventos = eventos

    async def __aenter__(self):
        self._eventos.append("tx.start")

    async def __aexit__(self, exc_type, exc, tb):
        self._eventos.append("tx.rollback" if exc_type else "tx.commit")
        return False


class _FakeConn:
    def __init__(self, eventos):
        self._eventos = eventos

    def transaction(self):
        return _FakeTx(self._eventos)


class _FakeAcquire:
    def __init__(self, eventos):
        self._eventos = eventos

    async def __aenter__(self):
        self._eventos.append("pool.acquire")
        return _FakeConn(self._eventos)

    async def __aexit__(self, exc_type, exc, tb):
        self._eventos.append("pool.release")
        return False


class _FakePool:
    def __init__(self, eventos):
        self._eventos = eventos

    def acquire(self):
        return _FakeAcquire(self._eventos)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """Arquivo de OCR real no disco + todos os efeitos externos falsificados."""
    arquivo = tmp_path / "ocr.txt"
    arquivo.write_text(OCR, encoding="utf-8")

    eventos: list[str] = []
    chamadas: dict = {}
    pool = _FakePool(eventos)

    async def criar_pool():
        eventos.append("criar_pool")

    async def fechar_pool():
        eventos.append("fechar_pool")

    async def criar_cliente():
        eventos.append("criar_cliente")

    async def fechar_cliente():
        eventos.append("fechar_cliente")

    async def gerar_embeddings(textos, *, caminho):
        eventos.append("rede")
        chamadas["caminho"] = caminho
        return [[0.1]] * len(textos)

    async def buscar_id_por_slug(conn, slug):
        eventos.append("slug")
        chamadas["slug"] = slug
        return ID_CONDOMINIO

    async def apagar_regras_do_documento(conn, *, condominio_id, documento):
        eventos.append("apagar")
        chamadas["apagar"] = (condominio_id, documento)
        return 2

    async def inserir_regras(conn, chunks, vetores, *, condominio_id):
        eventos.append("inserir")
        chamadas["inserir"] = (condominio_id, len(chunks), len(vetores))

    monkeypatch.setattr(ingestao, "criar_pool", criar_pool)
    monkeypatch.setattr(ingestao, "fechar_pool", fechar_pool)
    monkeypatch.setattr(ingestao, "get_pool", lambda: pool)
    monkeypatch.setattr(ingestao, "buscar_id_por_slug", buscar_id_por_slug)
    monkeypatch.setattr(
        ingestao, "apagar_regras_do_documento", apagar_regras_do_documento
    )
    monkeypatch.setattr(ingestao, "inserir_regras", inserir_regras)
    monkeypatch.setattr(embeddings, "criar_cliente", criar_cliente)
    monkeypatch.setattr(embeddings, "fechar_cliente", fechar_cliente)
    monkeypatch.setattr(embeddings, "gerar_embeddings", gerar_embeddings)

    return arquivo, eventos, chamadas, monkeypatch


# Uso da CLI (argparse)


def test_alvo_e_obrigatorio_e_exclusivo():
    with pytest.raises(SystemExit) as sem_alvo:
        ingestao.main(["x.txt", "--documento", DOC])
    assert sem_alvo.value.code == 2
    with pytest.raises(SystemExit) as dois_alvos:
        ingestao.main(["x.txt", "--documento", DOC, "--condominio", "a", "--geral"])
    assert dois_alvos.value.code == 2


@pytest.mark.parametrize("slug", ["", "   "])
def test_slug_em_branco_e_erro_de_uso_antes_de_ler_o_arquivo(slug, capsys):
    """Barrado no argparse: nem o read_text acontece, quanto menos o bootstrap."""
    with pytest.raises(SystemExit) as excinfo:
        ingestao.main(["nao-existe.txt", "--documento", DOC, "--condominio", slug])
    assert excinfo.value.code == 2
    assert "slug em branco" in capsys.readouterr().err


def test_slug_com_espaco_em_volta_e_normalizado(harness, capsys):
    arquivo, _, chamadas, _ = harness
    ingestao.main([str(arquivo), "--documento", DOC, "--condominio", "  res-gabro  "])
    assert chamadas["slug"] == "res-gabro"


def test_arquivo_inexistente_sai_limpo_com_codigo_1(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        ingestao.main([str(tmp_path / "nao-existe.txt"), "--documento", DOC, "--geral"])
    assert "erro ao ler" in str(excinfo.value.code)


def test_texto_sem_artigo_sai_limpo_com_a_causa(tmp_path):
    arquivo = tmp_path / "vazio.txt"
    arquivo.write_text("Texto corrido sem nenhuma estrutura legal.", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        ingestao.main([str(arquivo), "--documento", DOC, "--geral"])
    assert "nenhum artigo" in str(excinfo.value.code)


# Dry-run: auditoria sem efeitos externos


def test_dry_run_imprime_relatorio_e_nao_toca_nada(harness, capsys):
    arquivo, eventos, _, monkeypatch = harness

    def bomba(*args, **kwargs):
        raise AssertionError("dry-run tocou um efeito externo")

    for alvo in ("criar_pool", "get_pool"):
        monkeypatch.setattr(ingestao, alvo, bomba)
    for alvo in ("criar_cliente", "gerar_embeddings"):
        monkeypatch.setattr(embeddings, alvo, bomba)

    ingestao.main(
        [str(arquivo), "--documento", DOC, "--condominio", "res-gabro", "--dry-run"]
    )

    saida = capsys.readouterr().out
    assert "limpeza: 3 página(s)" in saida
    assert "1 de assinaturas" in saida
    assert "DAS PENALIDADES" in saida
    assert "chunks: 2 (2 artigo(s))" in saida
    assert f"{DOC}, Art. 1º" in saida
    assert "dry-run: nada foi enviado" in saida
    assert "FULANA" not in saida
    assert eventos == []


def test_dry_run_aviso_de_linha_suspeita_aparece_no_relatorio(tmp_path, capsys):
    arquivo = tmp_path / "suspeita.txt"
    arquivo.write_text(
        "Art. 1º - Regra com ressalva [ilegível] no meio.\n", encoding="utf-8"
    )
    ingestao.main([str(arquivo), "--documento", DOC, "--geral", "--dry-run"])
    saida = capsys.readouterr().out
    assert "AVISO — linha com colchete mantida" in saida
    assert "[ilegível]" in saida


# Regra de ouro: a ordem dos efeitos


def test_ordem_slug_solto_antes_da_rede_e_conn_so_depois_dos_vetores(harness, capsys):
    arquivo, eventos, chamadas, _ = harness
    ingestao.main([str(arquivo), "--documento", DOC, "--condominio", "res-gabro"])
    assert eventos == [
        "criar_pool",
        "criar_cliente",
        "pool.acquire",
        "slug",
        "pool.release",
        "rede",
        "pool.acquire",
        "tx.start",
        "apagar",
        "inserir",
        "tx.commit",
        "pool.release",
        "fechar_cliente",
        "fechar_pool",
    ]
    assert chamadas["caminho"] == "ingestao"
    assert chamadas["slug"] == "res-gabro"
    assert chamadas["apagar"] == (ID_CONDOMINIO, DOC)
    assert chamadas["inserir"] == (ID_CONDOMINIO, 2, 2)
    saida = capsys.readouterr().out
    assert "2 chunks gravados, 2 antigos substituídos" in saida


def test_geral_pula_o_slug_e_grava_com_condominio_none(harness, capsys):
    arquivo, eventos, chamadas, _ = harness
    ingestao.main([str(arquivo), "--documento", DOC, "--geral"])
    assert "slug" not in eventos
    assert chamadas["apagar"] == (None, DOC)
    assert chamadas["inserir"][0] is None
    assert "escopo geral" in capsys.readouterr().out


def test_slug_inexistente_aborta_antes_da_rede_e_fecha_tudo(harness):
    arquivo, eventos, _, monkeypatch = harness

    async def nao_achou(conn, slug):
        eventos.append("slug")
        return None

    monkeypatch.setattr(ingestao, "buscar_id_por_slug", nao_achou)
    with pytest.raises(SystemExit) as excinfo:
        ingestao.main([str(arquivo), "--documento", DOC, "--condominio", "res-gabo"])
    assert "'res-gabo' não encontrado" in str(excinfo.value.code)
    assert "rede" not in eventos
    assert eventos[-2:] == ["fechar_cliente", "fechar_pool"]


def test_falha_na_rede_sobe_crua_mas_fecha_cliente_e_pool(harness):
    arquivo, eventos, _, monkeypatch = harness

    async def rede_caiu(textos, *, caminho):
        eventos.append("rede")
        raise RuntimeError("timeout simulado")

    monkeypatch.setattr(embeddings, "gerar_embeddings", rede_caiu)
    with pytest.raises(RuntimeError, match="timeout simulado"):
        ingestao.main([str(arquivo), "--documento", DOC, "--geral"])
    assert "apagar" not in eventos
    assert eventos[-2:] == ["fechar_cliente", "fechar_pool"]
