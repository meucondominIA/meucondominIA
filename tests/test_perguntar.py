"""Testes da CLI de demonstração perguntar.py (Fase 2 · Passo 7).

Fakes no namespace do módulo: aqui só interessa a orquestração (bootstrap
abre/fecha SEMPRE, slug inexistente sai limpo, trechos impressos com fonte e
distância). A busca em si é testada em test_busca.py.
"""

from uuid import uuid4

import pytest

import embeddings
from ferramentas import perguntar
from regras import RegraEncontrada

ID_CONDOMINIO = uuid4()


@pytest.fixture
def harness(monkeypatch):
    eventos: list[str] = []
    trechos: list[RegraEncontrada] = []

    async def criar_pool():
        eventos.append("criar_pool")

    async def fechar_pool():
        eventos.append("fechar_pool")

    async def criar_cliente():
        eventos.append("criar_cliente")

    async def fechar_cliente():
        eventos.append("fechar_cliente")

    class _Acquire:
        async def __aenter__(self):
            return "conn-fake"

        async def __aexit__(self, *exc):
            return False

    class _Pool:
        def acquire(self):
            return _Acquire()

    async def buscar_id_por_slug(conn, slug):
        return ID_CONDOMINIO if slug == "res-gabro" else None

    async def buscar_trechos(pergunta, condominio_id, limite=None):
        eventos.append("busca")
        return trechos

    monkeypatch.setattr(perguntar, "criar_pool", criar_pool)
    monkeypatch.setattr(perguntar, "fechar_pool", fechar_pool)
    monkeypatch.setattr(perguntar, "get_pool", lambda: _Pool())
    monkeypatch.setattr(perguntar, "buscar_id_por_slug", buscar_id_por_slug)
    monkeypatch.setattr(perguntar, "buscar_trechos", buscar_trechos)
    monkeypatch.setattr(embeddings, "criar_cliente", criar_cliente)
    monkeypatch.setattr(embeddings, "fechar_cliente", fechar_cliente)
    return eventos, trechos


def test_condominio_e_obrigatorio():
    with pytest.raises(SystemExit) as excinfo:
        perguntar.main(["Pode ter cachorro?"])
    assert excinfo.value.code == 2


@pytest.mark.parametrize("slug", ["", "   "])
def test_slug_em_branco_e_erro_de_uso_sem_bootstrap(slug, monkeypatch, capsys):
    """Sai em 2 (uso), não em traceback — e sem abrir pool nem cliente OpenAI."""

    def bomba(*_args, **_kwargs):
        raise AssertionError("slug em branco chegou ao bootstrap")

    monkeypatch.setattr(perguntar, "criar_pool", bomba)
    monkeypatch.setattr(embeddings, "criar_cliente", bomba)

    with pytest.raises(SystemExit) as excinfo:
        perguntar.main(["Pode?", "--condominio", slug])
    assert excinfo.value.code == 2
    assert "slug em branco" in capsys.readouterr().err


def test_slug_com_espaco_em_volta_e_normalizado(harness):
    eventos, _ = harness
    perguntar.main(["Pode?", "--condominio", "  res-gabro  "])
    assert "busca" in eventos


def test_slug_inexistente_sai_limpo_e_fecha_tudo(harness):
    eventos, _ = harness
    with pytest.raises(SystemExit) as excinfo:
        perguntar.main(["Pode?", "--condominio", "nao-existe"])
    assert "'nao-existe' não encontrado" in str(excinfo.value.code)
    assert "busca" not in eventos
    assert eventos[-2:] == ["fechar_cliente", "fechar_pool"]


def test_imprime_fonte_distancia_e_conteudo(harness, capsys):
    eventos, trechos = harness
    trechos.append(
        RegraEncontrada(
            conteudo="Artigo 45º - É permitido animal doméstico...",
            fonte="Regimento, Art. 45",
            distancia=0.391,
        )
    )
    perguntar.main(["Pode ter cachorro?", "--condominio", "res-gabro"])
    saida = capsys.readouterr().out
    assert "[0.391] Regimento, Art. 45" in saida
    assert "É permitido animal doméstico" in saida
    assert eventos[-2:] == ["fechar_cliente", "fechar_pool"]


def test_base_vazia_avisa_sem_erro(harness, capsys):
    perguntar.main(["Pode?", "--condominio", "res-gabro"])
    assert "nenhuma regra ingerida" in capsys.readouterr().out
