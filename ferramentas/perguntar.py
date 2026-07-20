"""CLI de demonstração (D7): pergunta de morador → trechos citáveis (Passo 7).

Fora do caminho de produção — é a vitrine do retrieval: mesma busca que a
Fase 3 vai consumir (busca.buscar_trechos), com bootstrap próprio espelhando
a ingestao.py. Custa 1 embedding por pergunta.

Uso: venv/bin/python -m ferramentas.perguntar "Pode ter cachorro?" --condominio res-gabro
Saída: 0 sucesso · 1 falha de operação · 2 erro de uso (argparse — slug em
branco incluso, barrado no type= antes de qualquer bootstrap).
"""

import argparse
import asyncio
import sys

import embeddings
from busca import RegraEncontrada, buscar_trechos
from condominios import buscar_id_por_slug
from db import criar_pool, fechar_pool, get_pool


def _slug_condominio(valor: str) -> str:
    slug = valor.strip()
    if not slug:
        raise argparse.ArgumentTypeError("slug em branco: não identifica condomínio")
    return slug


def _montar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="perguntar",
        description="Busca os trechos de regras mais próximos de uma pergunta.",
    )
    parser.add_argument("pergunta", help="pergunta como um morador escreveria")
    parser.add_argument(
        "--condominio",
        metavar="SLUG",
        required=True,
        type=_slug_condominio,
        help="slug do condomínio",
    )
    parser.add_argument(
        "--k", type=int, default=None, help="quantos trechos (default: rag_top_k)"
    )
    return parser


async def _consultar(
    pergunta: str, slug: str, k: int | None
) -> list[RegraEncontrada] | None:
    """Devolve os trechos, ou None se o slug não existir."""
    await criar_pool()
    await embeddings.criar_cliente()
    try:
        async with get_pool().acquire() as conn:
            condominio_id = await buscar_id_por_slug(conn, slug)
        if condominio_id is None:
            return None
        return await buscar_trechos(pergunta, condominio_id, limite=k)
    finally:
        await embeddings.fechar_cliente()
        await fechar_pool()


def main(argv: list[str] | None = None) -> None:
    args = _montar_parser().parse_args(argv)
    trechos = asyncio.run(_consultar(args.pergunta, args.condominio, args.k))
    if trechos is None:
        sys.exit(
            f"condomínio {args.condominio!r} não encontrado — confira o slug "
            "na tabela condominios"
        )
    if not trechos:
        print("nenhuma regra ingerida para este condomínio.")
        return
    for trecho in trechos:
        print(f"[{trecho.distancia:.3f}] {trecho.fonte}")
        print(trecho.conteudo)
        print()


if __name__ == "__main__":
    main()
