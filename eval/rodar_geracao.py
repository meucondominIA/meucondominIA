"""Harness de avaliação da GERAÇÃO (Fase 3 · Passo 8) — roda na mão, custa tokens.

Cada caso roda responder_duvida de PRODUÇÃO (busca real + guarda D5 real) e é
medido por código (metricas_geracao) e pelo juiz (juiz.py, modelo mais forte).
Os trechos para o juiz vêm de buscar_trechos com a mesma pergunta — o embedding
é determinístico, então são os mesmos que a geração viu por dentro.

Eixos sem tocar código fechado: modelo/esforço via env (OPENAI_CHAT_MODEL,
OPENAI_CHAT_EFFORT — precedência sobre .env); tamanho do histórico via --trocas
(o histórico roteirizado do golden é parâmetro de responder_duvida).

NUNCA entra no pytest default — ferramenta de medição, não teste de regressão.

Uso: venv/bin/python -m eval.rodar_geracao [--golden eval/golden_geracao.json]
     [--reps 3] [--trocas 3] [--sem-juiz] [--juiz-modelo M] [--casos id1,id2]
     [--json saida.json]
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Self

import openai
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

import chat
import embeddings
from busca import buscar_trechos
from condominios import buscar_id_por_slug
from config import settings
from contexto import Troca
from db import criar_pool, fechar_pool, get_pool
from embeddings import EmbeddingIndisponivelError, EmbeddingRespostaError
from eval.juiz import MODELO_PADRAO, JuizError, julgar_fidelidade, julgar_nao_sei
from eval.metricas_geracao import Execucao, Portoes, avaliar_resposta, resumir
from geracao import responder_duvida


class ErroDeOperacao(Exception):
    """Falha esperada de operação: mensagem limpa no stderr e exit 1."""


class TrocaGolden(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pergunta: str
    resposta: str


class CasoGeracao(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    tipo: Literal["com_resposta", "sem_resposta"]
    pergunta: str
    esperadas: tuple[str, ...] = ()
    historico: tuple[TrocaGolden, ...] = ()
    limitacao_conhecida: bool = False

    @model_validator(mode="after")
    def _tipo_coerente(self) -> Self:
        if self.tipo == "com_resposta" and not self.esperadas:
            raise ValueError(f"caso {self.id!r}: com_resposta exige esperadas")
        if self.tipo == "sem_resposta" and self.esperadas:
            raise ValueError(f"caso {self.id!r}: sem_resposta não leva esperadas")
        return self


class GoldenGeracao(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    condominio: str
    casos: tuple[CasoGeracao, ...]

    @model_validator(mode="after")
    def _ids_unicos(self) -> Self:
        ids = [caso.id for caso in self.casos]
        repetidos = {i for i in ids if ids.count(i) > 1}
        if repetidos:
            raise ValueError(f"ids repetidos no golden: {sorted(repetidos)}")
        return self


def _montar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rodar_geracao",
        description="Avalia a geração contra o golden set (produção real).",
    )
    parser.add_argument("--golden", type=Path, default=Path("eval/golden_geracao.json"))
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument(
        "--trocas", type=int, default=3, help="trocas do histórico (eixo B7: 0..3)"
    )
    parser.add_argument("--sem-juiz", action="store_true")
    parser.add_argument("--juiz-modelo", default=MODELO_PADRAO)
    parser.add_argument(
        "--casos", default=None, help="ids separados por vírgula (subconjunto)"
    )
    parser.add_argument("--json", type=Path, default=None, metavar="SAIDA")
    return parser


async def _rodar(
    golden: GoldenGeracao, args: argparse.Namespace
) -> list[Execucao]:
    await criar_pool()
    await embeddings.criar_cliente()
    await chat.criar_cliente()
    cliente_juiz = AsyncOpenAI(api_key=settings.openai_api_key)
    try:
        async with get_pool().acquire() as conn:
            condominio_id = await buscar_id_por_slug(conn, golden.condominio)
        if condominio_id is None:
            raise ErroDeOperacao(f"condomínio {golden.condominio!r} não encontrado")

        execucoes = []
        for caso in golden.casos:
            historico = [
                Troca(pergunta=t.pergunta, resposta=t.resposta)
                for t in caso.historico
            ]
            historico = historico[-args.trocas :] if args.trocas else []
            try:
                trechos = await buscar_trechos(caso.pergunta, condominio_id)
            except (EmbeddingIndisponivelError, EmbeddingRespostaError) as exc:
                raise ErroDeOperacao(
                    f"busca indisponível no caso {caso.id!r} (cota/rede da "
                    f"OpenAI?): {exc}"
                ) from exc
            for rep in range(1, args.reps + 1):
                inicio = time.perf_counter()
                resposta = await responder_duvida(
                    caso.pergunta, condominio_id, historico
                )
                latencia = time.perf_counter() - inicio
                metricas = avaliar_resposta(resposta, caso.esperadas, caso.tipo)

                fiel = nao_sei = None
                justificativa = ""
                if not args.sem_juiz and not (
                    metricas.derrubada_d5 or metricas.contingencia
                ):
                    try:
                        if caso.tipo == "sem_resposta" or metricas.sem_fonte:
                            v = await julgar_nao_sei(
                                cliente_juiz, args.juiz_modelo, trechos, resposta
                            )
                            nao_sei = v.aprovado
                        else:
                            v = await julgar_fidelidade(
                                cliente_juiz, args.juiz_modelo, trechos, resposta
                            )
                            fiel = v.aprovado
                        justificativa = v.justificativa
                    except (JuizError, openai.APIError) as exc:
                        justificativa = f"juiz indisponível: {exc}"

                execucao = Execucao(
                    caso_id=caso.id,
                    tipo=caso.tipo,
                    rep=rep,
                    resposta=resposta,
                    metricas=metricas,
                    latencia_s=round(latencia, 2),
                    limitacao=caso.limitacao_conhecida,
                    juiz_fiel=fiel,
                    juiz_nao_sei=nao_sei,
                    juiz_justificativa=justificativa,
                )
                execucoes.append(execucao)
                _imprimir_execucao(execucao)
        return execucoes
    finally:
        await cliente_juiz.close()
        await chat.fechar_cliente()
        await embeddings.fechar_cliente()
        await fechar_pool()


def _imprimir_execucao(e: Execucao) -> None:
    m = e.metricas
    if m.contingencia:
        simbolo = "!"
    elif m.derrubada_d5:
        simbolo = "▼"
    elif e.juiz_fiel is False or e.juiz_nao_sei is False:
        simbolo = "✗"
    elif e.tipo == "com_resposta" and not m.citacao_correta:
        simbolo = "~"
    else:
        simbolo = "✓"
    print(
        f"{simbolo} {e.caso_id} r{e.rep} [{e.latencia_s:.1f}s] "
        f"fontes={list(m.fontes) or '—'}"
    )
    if simbolo in "✗~":
        print(f"    resposta: {e.resposta[:160]}")
        if e.juiz_justificativa:
            print(f"    juiz: {e.juiz_justificativa[:160]}")


def _imprimir_portoes(p: Portoes, execucoes: list[Execucao]) -> None:
    def farol(ok: bool) -> str:
        return "PASSA" if ok else "FALHA"

    print("\nportões (23/07/2026):")
    print(f"  citação correta      {p.citacao_correta_pct:6.1%}  ≥90%   {farol(p.citacao_correta_ok)}")
    print(f"  não-sei honesto      {p.nao_sei_pct:6.1%}  =100%  {farol(p.nao_sei_ok)}")
    print(f"  substantiva s/ cit.  {p.substantivas_sem_citacao:6d}  =0     {farol(p.substantivas_ok)}")
    print(f"  tamanho ≤900         {p.tamanho_pct:6.1%}  ≥95%   {farol(p.tamanho_ok)}")
    print(f"  informativos: derrubadas D5 {p.derrubadas_d5} · contingências {p.contingencias}")
    if p.sentinelas:
        print(
            f"  sentinelas (limitação conhecida, fora dos portões): "
            f"{p.sentinelas_passando}/{p.sentinelas} passando"
        )
    if p.fidelidade_pendente_revisao:
        print(f"\nREVISÃO HUMANA ({p.reprovadas_pelo_juiz} reprovadas pelo juiz):")
        for e in execucoes:
            if e.juiz_fiel is False or e.juiz_nao_sei is False:
                print(f"  - {e.caso_id} r{e.rep}: {e.juiz_justificativa[:200]}")
                print(f"    resposta: {e.resposta[:200]}")
    else:
        print("  fidelidade: zero reprovações do juiz — portão PASSA sem revisão")


def main(argv: list[str] | None = None) -> None:
    args = _montar_parser().parse_args(argv)
    try:
        bruto = json.loads(args.golden.read_text(encoding="utf-8"))
        golden = GoldenGeracao.model_validate(bruto)
    except OSError as exc:
        sys.exit(f"erro ao ler {args.golden}: {exc}")
    except (json.JSONDecodeError, ValidationError) as exc:
        sys.exit(f"golden inválido em {args.golden}: {exc}")

    if args.casos:
        ids = {i.strip() for i in args.casos.split(",")}
        desconhecidos = ids - {c.id for c in golden.casos}
        if desconhecidos:
            sys.exit(f"ids não existem no golden: {sorted(desconhecidos)}")
        golden = golden.model_copy(
            update={"casos": tuple(c for c in golden.casos if c.id in ids)}
        )

    config = {
        "quando": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "modelo": settings.openai_chat_model,
        "esforco": settings.openai_chat_effort,
        "verbosidade": settings.openai_chat_verbosity,
        "juiz": None if args.sem_juiz else args.juiz_modelo,
        "trocas": args.trocas,
        "reps": args.reps,
        "casos": len(golden.casos),
    }
    print(f"config: {json.dumps(config, ensure_ascii=False)}\n")

    try:
        execucoes = asyncio.run(_rodar(golden, args))
    except ErroDeOperacao as exc:
        sys.exit(str(exc))

    portoes = resumir(execucoes)
    _imprimir_portoes(portoes, execucoes)

    if args.json is not None:
        args.json.write_text(
            json.dumps(
                {
                    "config": config,
                    "portoes": portoes.model_dump(),
                    "execucoes": [e.model_dump() for e in execucoes],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nresultados gravados em {args.json}")

    decidiveis = (
        portoes.citacao_correta_ok,
        portoes.nao_sei_ok,
        portoes.substantivas_ok,
        portoes.tamanho_ok,
    )
    if not all(decidiveis):
        sys.exit("PORTÃO REPROVADO — fase não declarada pronta")


if __name__ == "__main__":
    main()
