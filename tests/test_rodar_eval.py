"""Testes do harness de eval (Fase 2 · Passo 7).

Sem rede e sem banco: buscar_trechos e o bootstrap são fakes. O golden.json
REAL do repo é validado aqui (formato, fontes não-vazias) — se alguém quebrar
o gabarito, a suíte acusa antes da rodada paga. Vazamento derruba com exit 1.
"""

import json
from pathlib import Path
from uuid import uuid4

import pytest

import embeddings
from eval import rodar_eval
from regras import RegraEncontrada

GOLDEN_REAL = Path(__file__).parent.parent / "eval" / "golden.json"
ID_CONDOMINIO = uuid4()

FONTES_TENANT = ["Doc, Art. 1º", "Doc, Art. 2º"]


def _golden(tmp_path, casos):
    caminho = tmp_path / "golden.json"
    caminho.write_text(
        json.dumps({"condominio": "res-gabro", "casos": casos}), encoding="utf-8"
    )
    return caminho


@pytest.fixture
def harness(monkeypatch):
    eventos: list[str] = []
    respostas: dict[str, list[RegraEncontrada]] = {}

    async def criar_pool():
        eventos.append("criar_pool")

    async def fechar_pool():
        eventos.append("fechar_pool")

    async def criar_cliente():
        eventos.append("criar_cliente")

    async def fechar_cliente():
        eventos.append("fechar_cliente")

    class _Conn:
        async def fetch(self, query, *args):
            return [{"fonte": f} for f in FONTES_TENANT]

    class _Acquire:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *exc):
            return False

    class _Pool:
        def acquire(self):
            return _Acquire()

    async def buscar_id_por_slug(conn, slug):
        return ID_CONDOMINIO if slug == "res-gabro" else None

    async def buscar_trechos(pergunta, condominio_id, limite=None):
        eventos.append("busca")
        return respostas.get(pergunta, [])

    monkeypatch.setattr(rodar_eval, "criar_pool", criar_pool)
    monkeypatch.setattr(rodar_eval, "fechar_pool", fechar_pool)
    monkeypatch.setattr(rodar_eval, "get_pool", lambda: _Pool())
    monkeypatch.setattr(rodar_eval, "buscar_id_por_slug", buscar_id_por_slug)
    monkeypatch.setattr(rodar_eval, "buscar_trechos", buscar_trechos)
    monkeypatch.setattr(embeddings, "criar_cliente", criar_cliente)
    monkeypatch.setattr(embeddings, "fechar_cliente", fechar_cliente)
    return eventos, respostas


def _regra(fonte, distancia):
    return RegraEncontrada(
        conteudo=f"Texto de {fonte}", fonte=fonte, distancia=distancia
    )


# Golden real do repo


def test_golden_real_e_valido_e_tem_perguntas_sem_gabarito():
    golden = rodar_eval.Golden.model_validate(
        json.loads(GOLDEN_REAL.read_text(encoding="utf-8"))
    )
    assert golden.condominio == "res-gabro"
    assert len(golden.casos) >= 15
    assert all(caso.pergunta.strip() for caso in golden.casos)
    assert all(all(f.strip() for f in caso.esperadas) for caso in golden.casos)
    assert any(not caso.esperadas for caso in golden.casos)


# Erros de carga


def test_golden_ausente_sai_limpo(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        rodar_eval.main(["--golden", str(tmp_path / "nao-existe.json")])
    assert "erro ao ler" in str(excinfo.value.code)


def test_golden_json_quebrado_sai_limpo(tmp_path):
    caminho = tmp_path / "golden.json"
    caminho.write_text("{quebrado", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        rodar_eval.main(["--golden", str(caminho)])
    assert "golden set inválido" in str(excinfo.value.code)


def test_chave_desconhecida_no_caso_derruba_em_vez_de_virar_sem_gabarito(
    harness, tmp_path
):
    """Typo em 'esperadas' sairia do denominador do hit@1 e inflaria a nota.

    Com o harness: se o guard regredir, a rodada segue nos fakes e o teste falha
    na hora — sem discar para o banco real.
    """
    caminho = _golden(tmp_path, [{"pergunta": "p1?", "esperados": ["Doc, Art. 1º"]}])
    with pytest.raises(SystemExit) as excinfo:
        rodar_eval.main(["--golden", str(caminho)])
    assert "golden set inválido" in str(excinfo.value.code)
    assert "esperados" in str(excinfo.value.code)


def test_chave_desconhecida_no_topo_derruba(harness, tmp_path):
    caminho = tmp_path / "golden.json"
    caminho.write_text(
        json.dumps({"condominio": "res-gabro", "casos": [], "condominios": ["outro"]}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as excinfo:
        rodar_eval.main(["--golden", str(caminho)])
    assert "golden set inválido" in str(excinfo.value.code)


# Rodada com fakes


def test_rodada_feliz_imprime_metricas_e_fecha_tudo(harness, tmp_path, capsys):
    eventos, respostas = harness
    respostas["p1?"] = [_regra("Doc, Art. 1º", 0.3)]
    respostas["p2?"] = [_regra("Doc, Art. 2º", 0.6)]
    golden = _golden(
        tmp_path,
        [
            {"pergunta": "p1?", "esperadas": ["Doc, Art. 1º"]},
            {"pergunta": "p2?", "esperadas": ["Doc, Art. 1º"]},
            {"pergunta": "p3?", "esperadas": []},
        ],
    )
    rodar_eval.main(["--golden", str(golden), "--k", "5"])
    saida = capsys.readouterr().out
    assert "hit@1 1/2" in saida
    assert "hit@5 1/2" in saida
    assert "vazamentos 0" in saida
    assert eventos.count("busca") == 3
    assert eventos[-2:] == ["fechar_cliente", "fechar_pool"]


def test_vazamento_derruba_com_exit_1(harness, tmp_path, capsys):
    _, respostas = harness
    respostas["p1?"] = [_regra("Sentinela de Vazamento (eval), Art. 1º", 0.2)]
    golden = _golden(tmp_path, [{"pergunta": "p1?", "esperadas": ["Doc, Art. 1º"]}])
    with pytest.raises(SystemExit) as excinfo:
        rodar_eval.main(["--golden", str(golden)])
    assert "VAZAMENTO DETECTADO" in str(excinfo.value.code)


def test_slug_inexistente_sai_limpo_e_fecha_tudo(harness, tmp_path):
    eventos, _ = harness
    caminho = tmp_path / "golden.json"
    caminho.write_text(
        json.dumps({"condominio": "nao-existe", "casos": [{"pergunta": "p?"}]}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as excinfo:
        rodar_eval.main(["--golden", str(caminho)])
    assert "não encontrado" in str(excinfo.value.code)
    assert eventos[-2:] == ["fechar_cliente", "fechar_pool"]


def test_saida_json_grava_resumo_e_casos(harness, tmp_path):
    _, respostas = harness
    respostas["p1?"] = [_regra("Doc, Art. 1º", 0.3)]
    golden = _golden(tmp_path, [{"pergunta": "p1?", "esperadas": ["Doc, Art. 1º"]}])
    saida = tmp_path / "resultado.json"
    rodar_eval.main(["--golden", str(golden), "--json", str(saida)])
    gravado = json.loads(saida.read_text(encoding="utf-8"))
    assert gravado["resumo"]["hit_1"] == 1
    assert gravado["casos"][0]["obtidas"] == ["Doc, Art. 1º"]
