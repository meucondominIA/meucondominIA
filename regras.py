"""Repositório de regras (asyncpg; recebe conn, como mensagens.py).

ÚNICO ponto do sistema que consulta por similaridade: até a RLS da Fase 5, o
isolamento entre condomínios É o WHERE desta query. Forma B (decisão de produto,
16/07/2026): só regras específicas do tenant — escopo 'geral' fica gravado mas
fora do retorno (risco de citar regra geral que a convenção sobrescreveu);
reativar é acrescentar OR escopo = 'geral', sem migration.

Sem índice vetorial a busca é exata (recall perfeito) e o WHERE elimina os
outros tenants antes de qualquer ordenação — semântica do SELECT (WHERE é o
passo 3; ORDER BY, 8; LIMIT, 9). <=> é distância de cosseno, a métrica
recomendada para os embeddings da OpenAI (normalizados, comprimento 1).

escopo é DERIVADO de condominio_id — a chk_regras_escopo torna os dois o mesmo
fato, então o par ilegal fica irrepresentável no chamador. O lote usa
executemany, atômico desde asyncpg 0.22 (entra tudo ou nada) sem transação
própria; o delete da reingestão mora aqui (apagar_regras_do_documento), mas a
transação que o junta ao insert é do chamador (Passo 5). O embedding da
pergunta nasce FORA daqui, antes da conn — nenhuma conexão do pool atravessa
I/O de rede.

No delete, `is not distinct from` trata NULL como valor: o escopo 'geral' tem
condominio_id NULL e `=` com NULL nunca é verdadeiro — o par (condominio_id,
documento) vira chave de substituição para os dois escopos com um SQL só. A
contagem devolvida vem do command tag "DELETE n" (formato do protocolo do
Postgres, não heurística).
"""

from uuid import UUID

import asyncpg
from pydantic import BaseModel, ConfigDict

from chunker import Chunk


class RegraEncontrada(BaseModel):
    model_config = ConfigDict(frozen=True)

    conteudo: str
    fonte: str
    distancia: float


async def inserir_regras(
    conn: asyncpg.Connection,
    chunks: list[Chunk],
    embeddings: list[list[float]],
    *,
    condominio_id: UUID | None,
) -> None:
    """Grava os chunks vetorizados; condominio_id None = escopo 'geral'."""
    if len(chunks) != len(embeddings):
        raise ValueError(
            f"chunks e embeddings desalinhados: {len(chunks)} chunks, "
            f"{len(embeddings)} embeddings"
        )
    if not chunks:
        return
    escopo = "geral" if condominio_id is None else "especifico"
    await conn.executemany(
        """
        insert into regras
          (condominio_id, escopo, conteudo, fonte, documento, metadata, embedding)
        values ($1, $2, $3, $4, $5, $6, $7)
        """,
        [
            (
                condominio_id,
                escopo,
                chunk.conteudo,
                chunk.fonte,
                chunk.documento,
                chunk.metadata,
                embedding,
            )
            for chunk, embedding in zip(chunks, embeddings, strict=True)
        ],
    )


async def apagar_regras_do_documento(
    conn: asyncpg.Connection,
    *,
    condominio_id: UUID | None,
    documento: str,
) -> int:
    """Apaga as regras do documento; devolve quantas saíram (0 = documento novo)."""
    documento = documento.strip()
    if not documento:
        raise ValueError("documento em branco: não identifica o que apagar")
    status = await conn.execute(
        """
        delete from regras
        where condominio_id is not distinct from $1
          and documento = $2
        """,
        condominio_id,
        documento,
    )
    return int(status.removeprefix("DELETE "))


async def buscar_por_similaridade(
    conn: asyncpg.Connection,
    embedding_pergunta: list[float],
    condominio_id: UUID,
    *,
    limite: int,
) -> list[RegraEncontrada]:
    """As `limite` regras do condomínio mais próximas da pergunta (distância asc)."""
    if limite < 1:
        raise ValueError(f"limite deve ser >= 1; recebi {limite}")
    rows = await conn.fetch(
        """
        select conteudo, fonte, embedding <=> $1 as distancia
        from regras
        where condominio_id = $2
        order by embedding <=> $1
        limit $3
        """,
        embedding_pergunta,
        condominio_id,
        limite,
    )
    return [
        RegraEncontrada(
            conteudo=row["conteudo"],
            fonte=row["fonte"],
            distancia=row["distancia"],
        )
        for row in rows
    ]
