"""Harness de avaliação do RAG (Fase 2 · Passo 7) — roda na mão, custa tokens.

Prova o critério de pronto da fase com números: cada pergunta do golden set
vai à busca REAL (embedding OpenAI + SELECT no Supabase) e o resultado é
confrontado com o gabarito. NUNCA entra no pytest default — é ferramenta de
medição, não teste de regressão.

O golden é validado na borda (parse, don't validate) com extra="forbid": chave
desconhecida é ERRO, não descarte silencioso — um typo em "esperadas" viraria
caso sem gabarito, sairia do denominador e INFLARIA o hit@1 em silêncio.

As fontes do tenant são lidas do banco no início e viram o universo contra o
qual vazamento é checado
— fonte devolvida fora dele derruba a rodada com exit 1 (invariante); métricas
de qualidade são informação, não portão. Bootstrap espelho da ingestao.py.

Uso: venv/bin/python -m eval.rodar_eval [--golden eval/golden.json]
     [--k N] [--json saida.json]
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from statistics import mean

from pydantic import BaseModel, ConfigDict, ValidationError

import embeddings
from busca import buscar_trechos
from condominios import buscar_id_por_slug
from config import settings
from db import criar_pool, fechar_pool, get_pool
from eval.metricas import ResultadoCaso, Resumo, avaliar_caso, resumir


class ErroDeOperacao(Exception):
    """Falha esperada de operação: vira mensagem limpa no stderr e exit 1."""


class CasoGolden(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pergunta: str
    esperadas: tuple[str, ...] = ()


class Golden(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    condominio: str
    casos: tuple[CasoGolden, ...]


def _montar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rodar_eval",
        description="Avalia o retrieval contra o golden set (embeddings reais).",
    )
    parser.add_argument(
        "--golden",
        type=Path,
        default=Path("eval/golden.json"),
        help="caminho do golden set (default: eval/golden.json)",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=None,
        help="top-k da busca (default: rag_top_k da config)",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        metavar="SAIDA",
        help="grava resultados + resumo neste arquivo JSON",
    )
    return parser


async def _rodar(golden: Golden, k: int) -> list[ResultadoCaso]:
    await criar_pool()
    await embeddings.criar_cliente()
    try:
        async with get_pool().acquire() as conn:
            condominio_id = await buscar_id_por_slug(conn, golden.condominio)
            if condominio_id is None:
                raise ErroDeOperacao(f"condomínio {golden.condominio!r} não encontrado")
            rows = await conn.fetch(
                "select distinct fonte from regras where condominio_id = $1",
                condominio_id,
            )
        fontes_do_tenant = frozenset(row["fonte"] for row in rows)
        if not fontes_do_tenant:
            raise ErroDeOperacao(
                f"nenhuma regra ingerida para {golden.condominio!r} — "
                "rode a ingestão antes do eval"
            )

        resultados = []
        for caso in golden.casos:
            trechos = await buscar_trechos(caso.pergunta, condominio_id, limite=k)
            resultados.append(
                avaliar_caso(
                    caso.pergunta,
                    caso.esperadas,
                    tuple(t.fonte for t in trechos),
                    tuple(t.distancia for t in trechos),
                    fontes_do_tenant,
                )
            )
        return resultados
    finally:
        await embeddings.fechar_cliente()
        await fechar_pool()


def _faixa(distancias: tuple[float, ...]) -> str:
    if not distancias:
        return "—"
    return (
        f"min {min(distancias):.3f} · média {mean(distancias):.3f} · "
        f"max {max(distancias):.3f} (n={len(distancias)})"
    )


def _imprimir(resultados: list[ResultadoCaso], resumo: Resumo, k: int) -> None:
    for r in resultados:
        if r.sem_gabarito:
            simbolo = "◌"
        elif r.hit_1 and r.completude:
            simbolo = "✓"
        elif r.hit_k:
            simbolo = "~"
        else:
            simbolo = "✗"
        top1 = f"{r.distancias[0]:.3f}" if r.distancias else "—"
        print(f"{simbolo} [{top1}] {r.pergunta}")
        if not r.sem_gabarito and not (r.hit_1 and r.completude):
            print(f"    esperadas: {', '.join(r.esperadas)}")
            print(f"    obtidas:   {', '.join(r.obtidas)}")
    n = resumo.com_gabarito
    print(
        f"\nresumo (k={k}): hit@1 {resumo.hit_1}/{n} · hit@{k} {resumo.hit_k}/{n} · "
        f"completude@{k} {resumo.completude}/{n} · vazamentos {resumo.vazamentos}"
    )
    print(f"  distâncias dos acertos:        {_faixa(resumo.dist_acertos)}")
    print(f"  top-1 dos erros:               {_faixa(resumo.dist_top1_erros)}")
    print(f"  top-1 das perguntas sem regra: {_faixa(resumo.dist_sem_gabarito)}")


def main(argv: list[str] | None = None) -> None:
    args = _montar_parser().parse_args(argv)
    k = args.k if args.k is not None else settings.rag_top_k

    try:
        bruto = json.loads(args.golden.read_text(encoding="utf-8"))
        golden = Golden.model_validate(bruto)
    except OSError as exc:
        sys.exit(f"erro ao ler {args.golden}: {exc}")
    except (json.JSONDecodeError, ValidationError) as exc:
        sys.exit(f"golden set inválido em {args.golden}: {exc}")

    try:
        resultados = asyncio.run(_rodar(golden, k))
    except ErroDeOperacao as exc:
        sys.exit(str(exc))

    resumo = resumir(resultados)
    _imprimir(resultados, resumo, k)

    if args.json is not None:
        args.json.write_text(
            json.dumps(
                {
                    "resumo": resumo.model_dump(),
                    "casos": [r.model_dump() for r in resultados],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"resultados gravados em {args.json}")

    if resumo.vazamentos:
        sys.exit(
            f"VAZAMENTO DETECTADO: {resumo.vazamentos} fonte(s) de fora do "
            f"tenant {golden.condominio!r} — invariante violado"
        )


if __name__ == "__main__":
    main()
