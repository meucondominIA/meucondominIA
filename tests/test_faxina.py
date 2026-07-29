"""Testes do ciclo da faxina (Fase 4 · Etapa 4).

Sem banco e sem rede: o que se prova aqui é a coreografia — que a conexão é
DEVOLVIDA antes da chamada de rede, e que um ciclo sem órfãos não chama o
Storage. A query em si é do teste de integração, onde há Postgres de verdade.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

import anexos
import faxina


class _FakeConn:
    def __init__(self, pool, nomes):
        self.pool = pool
        self._nomes = nomes

    async def fetch(self, *args):
        return [{"name": n} for n in self._nomes]


class _FakeAcquire:
    def __init__(self, pool):
        self.pool = pool

    async def __aenter__(self):
        self.pool.em_uso = True
        return _FakeConn(self.pool, self.pool.nomes)

    async def __aexit__(self, *exc):
        self.pool.em_uso = False


class _FakePool:
    def __init__(self, nomes):
        self.nomes = nomes
        self.em_uso = False

    def acquire(self):
        return _FakeAcquire(self)


@pytest.fixture
def cenario(monkeypatch):
    def _montar(nomes, apagar=None):
        pool = _FakePool(nomes)
        monkeypatch.setattr(faxina, "get_pool", lambda: pool)
        monkeypatch.setattr(anexos, "apagar", apagar or AsyncMock())
        return pool

    return _montar


def test_remove_os_orfaos_encontrados(cenario):
    apagar = AsyncMock()
    cenario(["c/a.jpg", "c/b.jpg"], apagar)

    assert asyncio.run(faxina.limpar_orfaos()) == 2
    apagar.assert_awaited_once_with(["c/a.jpg", "c/b.jpg"])


def test_ciclo_vazio_nao_chama_o_storage(cenario):
    apagar = AsyncMock()
    cenario([], apagar)

    assert asyncio.run(faxina.limpar_orfaos()) == 0
    apagar.assert_not_awaited()


def test_apaga_com_a_conexao_ja_devolvida(cenario):
    """A regra de ouro: rede nunca com conexão do pool na mão."""
    visto = {}
    pool = None

    async def apagar(caminhos):
        visto["em_uso"] = pool.em_uso

    pool = cenario(["c/a.jpg"], apagar)
    asyncio.run(faxina.limpar_orfaos())
    assert visto["em_uso"] is False


def test_falha_do_storage_sobe_para_o_loop(cenario):
    async def apagar(caminhos):
        raise anexos.AnexoIndisponivelError("fora do ar")

    cenario(["c/a.jpg"], apagar)
    with pytest.raises(anexos.AnexoIndisponivelError):
        asyncio.run(faxina.limpar_orfaos())


def test_loop_sobrevive_a_um_ciclo_que_estoura(cenario, monkeypatch):
    """Um ciclo ruim não pode matar o job — o próximo tenta de novo."""
    ciclos = []

    async def ciclo():
        ciclos.append(1)
        if len(ciclos) == 1:
            raise RuntimeError("banco fora do ar")

    async def dormir(_):
        if len(ciclos) >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(faxina, "limpar_orfaos", ciclo)
    monkeypatch.setattr(faxina.asyncio, "sleep", dormir)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(faxina.rodar_faxina())
    assert len(ciclos) == 2
