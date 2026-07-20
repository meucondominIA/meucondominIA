"""Serviço de recuperação: pergunta → embedding → regras do condomínio (Passo 6).

A costura que a Fase 3 pluga no lugar do eco (A9: o processador segue no eco
até existir a identificação telefone → condomínio). O valor do serviço é a
ordem — regra de ouro do projeto: o embedding da pergunta nasce na rede SEM
nenhuma conexão do pool adquirida; a conn vem depois, curta, só para o SELECT
de similaridade (o pool compartilha o teto de conexões do Supabase).

Devolve RegraEncontrada, re-exportada daqui — a Fase 3 importa de busca, não
do repositório. O TrechoRegra do plano da fase (§4.4) morreu com a Forma B:
escopo seria a constante 'especifico' e metadata não tem leitor — campo morto
não nasce (decisão 17/07/2026, com o dono). Sem limiar de distância (D4):
acertos medidos entre 0,332–0,713 sobrepõem o top-1 errado (0,707) — cortar
agora mataria resposta certa; o eval do Passo 7 decide com dados.

Pergunta vazia é barrada pelo próprio adapter de embeddings, antes de qualquer
rede; limite inválido, pelo repositório — guardas moram onde o invariante
mora, sem duplicação aqui.
"""

from uuid import UUID

import embeddings
from config import settings
from db import get_pool
from regras import RegraEncontrada, buscar_por_similaridade


async def buscar_trechos(
    pergunta: str,
    condominio_id: UUID,
    limite: int | None = None,
) -> list[RegraEncontrada]:
    """As regras do condomínio mais próximas da pergunta (distância crescente).

    limite=None usa settings.rag_top_k (5 — medido: k=3 perde completude
    multi-artigo, k=8 é dominado).
    """
    if limite is None:
        limite = settings.rag_top_k
    [embedding_pergunta] = await embeddings.gerar_embeddings(
        [pergunta], caminho="busca"
    )
    async with get_pool().acquire() as conn:
        return await buscar_por_similaridade(
            conn, embedding_pergunta, condominio_id, limite=limite
        )
