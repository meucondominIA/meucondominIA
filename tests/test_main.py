"""Testes do lifespan (Fase 3 · Passo 1).

Sem rede e sem banco: cada criar_/fechar_ é trocado por um dublê que anota o que
foi chamado, então a ORDEM e as garantias do AsyncExitStack viram asserção em vez
de promessa. O sweeper falso anota o próprio cancelamento — é assim que se prova
que ele morre antes do pool, que é o recurso que ele usa.
"""

import asyncio

import pytest

import chat
import embeddings
import main


def _instrumentar(monkeypatch, log, *, criar_falha=None, fechar_falha=None):
    def _criar(nome):
        async def _f(*args, **kwargs):
            if nome == criar_falha:
                raise RuntimeError(f"falha ao criar {nome}")
            log.append(f"criou {nome}")

        return _f

    def _fechar(nome):
        async def _f(*args, **kwargs):
            log.append(f"fechou {nome}")
            if nome == fechar_falha:
                raise RuntimeError(f"falha ao fechar {nome}")

        return _f

    monkeypatch.setattr(main, "criar_pool", _criar("pool"))
    monkeypatch.setattr(main, "fechar_pool", _fechar("pool"))
    monkeypatch.setattr(main, "criar_cliente", _criar("zpro"))
    monkeypatch.setattr(main, "fechar_cliente", _fechar("zpro"))
    monkeypatch.setattr(embeddings, "criar_cliente", _criar("embeddings"))
    monkeypatch.setattr(embeddings, "fechar_cliente", _fechar("embeddings"))
    monkeypatch.setattr(chat, "criar_cliente", _criar("chat"))
    monkeypatch.setattr(chat, "fechar_cliente", _fechar("chat"))

    async def _sweeper_falso():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            log.append("sweeper cancelado")
            raise

    monkeypatch.setattr(main, "rodar_sweeper", _sweeper_falso)


def _subir_e_descer():
    async def _corpo():
        async with main.lifespan(main.app):
            # Sem ceder o controle, a task do sweeper seria cancelada ANTES de
            # começar a rodar — no servidor real ela roda por horas.
            await asyncio.sleep(0.01)

    asyncio.run(_corpo())


def test_cria_na_ordem_e_fecha_na_inversa(monkeypatch):
    log = []
    _instrumentar(monkeypatch, log)

    _subir_e_descer()

    assert log == [
        "criou pool",
        "criou zpro",
        "criou embeddings",
        "criou chat",
        "sweeper cancelado",
        "fechou chat",
        "fechou embeddings",
        "fechou zpro",
        "fechou pool",
    ]


def test_sweeper_morre_antes_do_pool(monkeypatch):
    log = []
    _instrumentar(monkeypatch, log)

    _subir_e_descer()

    assert log.index("sweeper cancelado") < log.index("fechou pool")


def test_falha_ao_criar_o_quarto_fecha_os_anteriores(monkeypatch):
    """OPENAI_API_KEY vazia estoura em AsyncOpenAI: o pool não pode vazar."""
    log = []
    _instrumentar(monkeypatch, log, criar_falha="chat")

    with pytest.raises(RuntimeError, match="falha ao criar chat"):
        _subir_e_descer()

    assert log == [
        "criou pool",
        "criou zpro",
        "criou embeddings",
        "fechou embeddings",
        "fechou zpro",
        "fechou pool",
    ]


def test_falha_ao_fechar_no_meio_nao_impede_os_outros(monkeypatch):
    log = []
    _instrumentar(monkeypatch, log, fechar_falha="embeddings")

    with pytest.raises(RuntimeError, match="falha ao fechar embeddings"):
        _subir_e_descer()

    assert "fechou zpro" in log
    assert "fechou pool" in log
    assert log[-1] == "fechou pool"


def test_falha_ao_fechar_o_pool_ainda_propaga(monkeypatch):
    """A pilha não engole a falha: quem sobe o serviço fica sabendo."""
    log = []
    _instrumentar(monkeypatch, log, fechar_falha="pool")

    with pytest.raises(RuntimeError, match="falha ao fechar pool"):
        _subir_e_descer()
