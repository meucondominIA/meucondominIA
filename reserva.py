"""Motor do wizard de reserva (Fase 4 · Etapa 3): função pura, zero I/O.

Recebe o passo atual + a mensagem + os dados que a casca já leu, e devolve o
próximo passo ou um DESCRITOR do I/O que falta. Não lê banco, não chama rede,
não olha relógio — por isso toda transição se testa sem custo.

Dois dados entram por caminhos diferentes, e a assimetria é do domínio:
`areas` chega como parâmetro (conjunto estável, reler dá a mesma numeração),
mas os dias livres NÃO — outro morador reserva entre duas telas, a lista
encolhe e o número 3 passaria a significar outra data. Por isso o mapeamento
número→data vai CONGELADO no rascunho, e o motor o lê de lá.

O D3 (dia inteiro) colapsa em `opcoes[n - 1]`: a lista veio de dias_livres, que
só devolve dia livre, então disponibilidade não é condição a testar — é
propriedade do conjunto de onde a data saiu.
"""

from datetime import date
from enum import Enum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from areas import AreaReservavel
from roteador import Mensagem, OpcaoConfirmacao, opcao

DIAS_POR_TELA = 7
VER_MAIS = 9

# Fora da faixa da lista, como OpcaoMenu.TROCAR_CONDOMINIO: no fim dela o número
# se deslocaria a cada tela de tamanho diferente.
assert DIAS_POR_TELA < VER_MAIS


class Passo(str, Enum):
    """Os sub-estados do wizard. Vivem no rascunho, não em conversas.estado —
    por isso mudar um passo não exige migration (D1)."""

    AREA = "area"
    DIA = "dia"
    CONFIRMACAO = "confirmacao"


class MensagemReserva(str, Enum):
    """Identidades que o MOTOR emite. O 0 do roteador emite Mensagem.NADA_AGENDADO.

    Valores prefixados: `str` enums de valor igual são == e têm o mesmo hash, então
    um valor repetido em Mensagem faria o braço errado do match capturar a
    mensagem — CONFIRMACAO_NAO_ENTENDIDA já existe lá.
    """

    ESCOLHER_AREA = "reserva_escolher_area"
    AREA_NAO_ENTENDIDA = "reserva_area_nao_entendida"
    SEM_AREAS = "reserva_sem_areas"
    LISTA_DIAS = "reserva_lista_dias"
    DIA_NAO_ENTENDIDO = "reserva_dia_nao_entendido"
    SEM_DATAS = "reserva_sem_datas"
    ULTIMAS_DATAS = "reserva_ultimas_datas"
    DATA_TOMADA = "reserva_data_tomada"
    CONFIRMAR = "reserva_confirmar"
    CONFIRMACAO_NAO_ENTENDIDA = "reserva_confirmacao_nao_entendida"
    RESERVA_REGISTRADA = "reserva_registrada"


class _Rascunho(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RascunhoArea(_Rascunho):
    """Vazio de propósito: nada foi escolhido, e um campo opcional aqui seria a
    porta para "passo=area mas já tem dia"."""

    passo: Literal[Passo.AREA] = Passo.AREA


class RascunhoDia(_Rascunho):
    """`opcoes` É o mapeamento congelado: o número N do morador é opcoes[N-1].

    `ultima` evita que o "ver mais" na última tela leia o banco para descobrir
    que não há nada.
    """

    passo: Literal[Passo.DIA] = Passo.DIA
    area_id: UUID
    pagina: int
    opcoes: list[date]
    ultima: bool


class RascunhoConfirmacao(_Rascunho):
    """`pagina` existe para a volta: se a data for tomada, a lista reabre onde o
    morador estava, não no começo."""

    passo: Literal[Passo.CONFIRMACAO] = Passo.CONFIRMACAO
    area_id: UUID
    dia: date
    pagina: int


Rascunho = Annotated[
    RascunhoArea | RascunhoDia | RascunhoConfirmacao,
    Field(discriminator="passo"),
]

_RASCUNHO = TypeAdapter(Rascunho)


def ler(bruto: dict[str, Any]) -> Rascunho:
    """jsonb do banco → objeto tipado. Passo desconhecido levanta em vez de
    virar palpite."""
    return _RASCUNHO.validate_python(bruto)


def gravar(rascunho: Rascunho) -> dict[str, Any]:
    """Objeto tipado → jsonb. `mode="json"` é obrigatório: o codec do db.py é
    json.dumps, que não serializa UUID nem date."""
    return rascunho.model_dump(mode="json")


class Continuar(BaseModel):
    """Segue no wizard. Renderizável só com `areas` + o rascunho — sem I/O novo."""

    model_config = ConfigDict(frozen=True)

    mensagem: MensagemReserva
    rascunho: Rascunho


class MostrarDias(BaseModel):
    """Descritor de I/O: a casca lê dias_livres e monta a página.

    Existe porque toda tela de datas precisa de uma leitura, e o rascunho dessa
    tela precisa do resultado dela — o motor não pode montá-lo sozinho.
    """

    model_config = ConfigDict(frozen=True)

    area_id: UUID
    pagina: int
    aviso: MensagemReserva | None = None


class Concluir(BaseModel):
    """Descritor de I/O: a casca transforma em criar_reserva_pendente.

    Só o dia e a área: tenant, fuso e telefone são da conversa, não da escolha.
    """

    model_config = ConfigDict(frozen=True)

    area_id: UUID
    dia: date


class Encerrar(BaseModel):
    """Sai do wizard e volta ao menu; o rascunho some."""

    model_config = ConfigDict(frozen=True)

    mensagem: MensagemReserva | Mensagem


Avanco = Continuar | MostrarDias | Concluir | Encerrar


class PaginaDias(BaseModel):
    """Uma tela de datas — o recorte que vira texto E rascunho."""

    model_config = ConfigDict(frozen=True)

    pagina: int
    dias: list[date]
    ultima: bool


def montar_pagina(dias: list[date], *, pagina: int) -> PaginaDias:
    """Recorta a janela já lida. Pura: não sabe que dia é hoje nem lê banco.

    Página além do fim (os dias encolheram entre telas) volta para a última não
    vazia, em vez de devolver tela em branco.
    """
    inicio = pagina * DIAS_POR_TELA
    fatia = list(dias[inicio : inicio + DIAS_POR_TELA])

    if not fatia and dias:
        pagina = (len(dias) - 1) // DIAS_POR_TELA
        inicio = pagina * DIAS_POR_TELA
        fatia = list(dias[inicio:])

    return PaginaDias(
        pagina=pagina, dias=fatia, ultima=inicio + DIAS_POR_TELA >= len(dias)
    )


def avancar(
    rascunho: Rascunho | None,
    escolha: str,
    *,
    areas: list[AreaReservavel],
) -> Avanco:
    """Decide o próximo passo. O que precisa de banco vira descritor.

    `rascunho is None` é a entrada no wizard — garantido pelo
    chk_conversas_rascunho, que só permite rascunho em estado='reserva'. Aí
    `escolha` é ignorada: o "2" veio do menu e não escolhe nada aqui dentro.
    """
    if rascunho is None:
        return _iniciar(areas)

    match rascunho:
        case RascunhoArea():
            return _area(escolha, areas)
        case RascunhoDia():
            return _dia(escolha, rascunho)
        case RascunhoConfirmacao():
            return _confirmacao(escolha, rascunho)


def _iniciar(areas: list[AreaReservavel]) -> Avanco:
    if not areas:
        return Encerrar(mensagem=MensagemReserva.SEM_AREAS)
    return Continuar(mensagem=MensagemReserva.ESCOLHER_AREA, rascunho=RascunhoArea())


def _area(escolha: str, areas: list[AreaReservavel]) -> Avanco:
    """Guarda o area_id (UUID), nunca o número — o número só vale naquela tela.

    O índice resolve contra a lista DEVOLVIDA, como atendimento._item faz com
    condomínios: nunca uma segunda consulta com OFFSET.
    """
    escolhido = opcao(escolha)
    if escolhido is None or not 1 <= escolhido <= len(areas):
        return Continuar(
            mensagem=MensagemReserva.AREA_NAO_ENTENDIDA, rascunho=RascunhoArea()
        )
    return MostrarDias(area_id=areas[escolhido - 1].id, pagina=0)


def _dia(escolha: str, rascunho: RascunhoDia) -> Avanco:
    """Só o "ver mais" com página seguinte toca o banco; o resto sai do rascunho.

    Escolha inválida remostra a MESMA tela com a MESMA numeração: re-numerar
    embaixo do morador é o bug que o congelamento existe para evitar.
    """
    escolhido = opcao(escolha)

    if escolhido == VER_MAIS:
        if rascunho.ultima:
            return Continuar(mensagem=MensagemReserva.ULTIMAS_DATAS, rascunho=rascunho)
        return MostrarDias(area_id=rascunho.area_id, pagina=rascunho.pagina + 1)

    if escolhido is not None and 1 <= escolhido <= len(rascunho.opcoes):
        return Continuar(
            mensagem=MensagemReserva.CONFIRMAR,
            rascunho=RascunhoConfirmacao(
                area_id=rascunho.area_id,
                dia=rascunho.opcoes[escolhido - 1],
                pagina=rascunho.pagina,
            ),
        )

    return Continuar(mensagem=MensagemReserva.DIA_NAO_ENTENDIDO, rascunho=rascunho)


def _confirmacao(escolha: str, rascunho: RascunhoConfirmacao) -> Avanco:
    """O cinto de segurança: a tela devolveu área e data por extenso antes disto.

    O 2 não cancela reserva nenhuma — nada foi gravado ainda; ele só não agenda.
    """
    match opcao(escolha):
        case OpcaoConfirmacao.SIM:
            return Concluir(area_id=rascunho.area_id, dia=rascunho.dia)
        case OpcaoConfirmacao.NAO:
            return Encerrar(mensagem=Mensagem.NADA_AGENDADO)
        case _:
            return Continuar(
                mensagem=MensagemReserva.CONFIRMACAO_NAO_ENTENDIDA, rascunho=rascunho
            )
