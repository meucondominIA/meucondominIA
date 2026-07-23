"""Costura entre o roteador (puro) e o banco (Fase 3 · Passos 3 e 7).

O roteador decide sem I/O e por isso DELEGA o que precisa do banco: qual
condomínio é o item N, o nome do candidato a citar — e, na dúvida, o histórico
da conversa. Aqui isso vira consulta, e a decisão vira (texto, transição) para
o processador enviar e gravar — ou GeracaoPendente, a chamada de geração
adiada que o processador dispara depois de devolver a conexão ao pool.

Regra de ouro: só banco, nenhuma rede. Envio, geração e gravação da saída são
do processador, fora de qualquer conexão presa.

A invariante do isolamento mora aqui: o índice N é resolvido sobre a lista
DEVOLVIDA por listar_elegiveis, nunca por uma segunda consulta com OFFSET.
"""

from datetime import datetime, timezone
from uuid import UUID

import asyncpg
from pydantic import BaseModel, ConfigDict

from condominios import CondominioElegivel, listar_elegiveis, nome_por_id
from config import settings
from contexto import MAX_TROCAS, Troca
from mensagens import ultimas_trocas
from roteador import (
    Conversa,
    Decisao,
    DelegarDuvida,
    DelegarIdentificacao,
    Mensagem,
    Responder,
    Transicao,
    rotear,
)
from textos import MensagemAtendimento, renderizar
from zpro_models import MessageType

_EXIGEM_LISTA = frozenset(
    {Mensagem.PEDIR_CONDOMINIO, Mensagem.CONDOMINIO_NAO_ENTENDIDO}
)
_EXIGEM_NOME = frozenset(
    {Mensagem.CONFIRMACAO_NAO_ENTENDIDA, Mensagem.RECONFIRMAR_CONDOMINIO}
)


class GeracaoPendente(BaseModel):
    """A geração adiada: o processador a dispara fora da conexão."""

    model_config = ConfigDict(frozen=True)

    pergunta: str
    condominio_id: UUID
    historico: list[Troca]


def _sessao_expirada(conversa: Conversa) -> bool:
    idade = datetime.now(timezone.utc) - conversa.ultima_interacao_em
    return idade.total_seconds() > settings.sessao_ttl_horas * 3600


async def responder(
    conn: asyncpg.Connection,
    conversa: Conversa,
    *,
    tipo: MessageType,
    texto: str | None,
) -> tuple[str, Transicao | None] | GeracaoPendente:
    """A resposta ao morador e a transição a gravar (None = estado inalterado),
    ou o pacote de geração que o processador resolve fora da conexão."""
    decisao = rotear(
        conversa,
        tipo=tipo,
        texto=texto,
        precisa_reconfirmar=_sessao_expirada(conversa),
    )
    return await _resolver(conn, conversa, decisao)


async def _resolver(
    conn: asyncpg.Connection, conversa: Conversa, decisao: Decisao
) -> tuple[str, Transicao | None] | GeracaoPendente:
    match decisao:
        case DelegarIdentificacao(indice=indice):
            return await _identificar(conn, indice)
        case DelegarDuvida(pergunta=pergunta):
            return GeracaoPendente(
                pergunta=pergunta,
                condominio_id=conversa.condominio_id,
                historico=await ultimas_trocas(conn, conversa.id, limite=MAX_TROCAS),
            )
        case Responder():
            return await _responder(conn, conversa, decisao)


async def _identificar(
    conn: asyncpg.Connection, indice: int
) -> tuple[str, Transicao | None]:
    """O morador escolheu o item N: se existir, entra em confirmação."""
    lista = await listar_elegiveis(conn)
    if not lista:
        return renderizar(MensagemAtendimento.SEM_CONDOMINIOS), None
    escolhido = _item(lista, indice)
    if escolhido is None:
        return renderizar(Mensagem.CONDOMINIO_NAO_ENTENDIDO, condominios=lista), None
    return (
        renderizar(
            MensagemAtendimento.CONFIRMAR_CONDOMINIO, nome_condominio=escolhido.nome
        ),
        Transicao.para_confirmacao(escolhido.id),
    )


def _item(lista: list[CondominioElegivel], indice: int) -> CondominioElegivel | None:
    """Indexa a lista já em mãos. O roteador barra indice < 1; o teto é daqui."""
    return lista[indice - 1] if 1 <= indice <= len(lista) else None


async def _responder(
    conn: asyncpg.Connection, conversa: Conversa, decisao: Responder
) -> tuple[str, Transicao | None]:
    """Uma decisão do roteador que já é resposta pronta. Só busca o contexto que
    a mensagem exige — a transição vem do roteador e passa intacta."""
    if decisao.mensagem in _EXIGEM_LISTA:
        lista = await listar_elegiveis(conn)
        if not lista:
            return renderizar(MensagemAtendimento.SEM_CONDOMINIOS), decisao.transicao
        return renderizar(decisao.mensagem, condominios=lista), decisao.transicao

    if decisao.mensagem in _EXIGEM_NOME:
        nome = await nome_por_id(conn, _id_do_candidato(conversa, decisao))
        return renderizar(decisao.mensagem, nome_condominio=nome), decisao.transicao

    return renderizar(decisao.mensagem), decisao.transicao


def _id_do_candidato(conversa: Conversa, decisao: Responder):
    """De onde vem o candidato a citar: a reconfirmação acabou de movê-lo para a
    transição; a repergunta da confirmação lê o pendente que já estava lá."""
    if decisao.mensagem is Mensagem.RECONFIRMAR_CONDOMINIO:
        return decisao.transicao.condominio_pendente
    return conversa.condominio_pendente
