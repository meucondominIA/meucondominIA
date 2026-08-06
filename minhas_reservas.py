"""Motor do fluxo "Minhas reservas": função pura, zero I/O.

Irmão de reserva.py e ocorrencia.py — recebe o passo atual + a mensagem, e
devolve o próximo passo ou um DESCRITOR do I/O que falta.

A lista vai CONGELADA no rascunho pelo mesmo motivo do wizard de dias, e por um
motivo a mais: ela encolhe sozinha com o tempo (o filtro é `fim > now()`).
Reconsultar entre a tela e o número deslocaria a numeração — e aqui isso não
mostra a data errada, cancela a reserva errada.

O rascunho da confirmação carrega o item inteiro, não só o id: a tela de
confirmação, a mensagem de sucesso e o aviso ao síndico saem todos dele, sem uma
segunda leitura.
"""

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from reservas import ReservaDoMorador
from roteador import Mensagem, OpcaoConfirmacao, opcao


class Passo(str, Enum):
    """Os sub-estados do fluxo. Vivem no rascunho, não em conversas.estado."""

    LISTA = "lista"
    CONFIRMACAO = "confirmacao"


class MensagemMinhasReservas(str, Enum):
    """Identidades que o MOTOR emite. O 0 do roteador emite Mensagem.NADA_CANCELADO.

    Valores prefixados pelo mesmo motivo de MensagemReserva: `str` enums de valor
    igual são == e têm o mesmo hash, e um valor repetido faria o braço errado do
    match capturar a mensagem.
    """

    LISTA = "minhas_lista"
    SEM_RESERVAS = "minhas_sem_reservas"
    RESERVA_NAO_ENTENDIDA = "minhas_reserva_nao_entendida"
    CONFIRMAR = "minhas_confirmar"
    CONFIRMACAO_NAO_ENTENDIDA = "minhas_confirmacao_nao_entendida"
    CANCELADA = "minhas_cancelada"
    JA_NAO_ATIVA = "minhas_ja_nao_ativa"


class _Rascunho(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RascunhoLista(_Rascunho):
    """`opcoes` É o mapeamento congelado: o número N do morador é opcoes[N-1]."""

    passo: Literal[Passo.LISTA] = Passo.LISTA
    opcoes: list[ReservaDoMorador]


class RascunhoConfirmacao(_Rascunho):
    passo: Literal[Passo.CONFIRMACAO] = Passo.CONFIRMACAO
    item: ReservaDoMorador


Rascunho = Annotated[
    RascunhoLista | RascunhoConfirmacao, Field(discriminator="passo")
]

_RASCUNHO = TypeAdapter(Rascunho)


def ler(bruto: dict[str, Any]) -> Rascunho:
    """jsonb do banco → objeto tipado. Passo desconhecido levanta."""
    return _RASCUNHO.validate_python(bruto)


def gravar(rascunho: Rascunho) -> dict[str, Any]:
    """Objeto tipado → jsonb. `mode="json"` é obrigatório: o codec do db.py é
    json.dumps, que não serializa UUID nem date."""
    return rascunho.model_dump(mode="json")


class MostrarLista(BaseModel):
    """Descritor de I/O: a casca lê listar_minhas e monta o rascunho.

    Sem campo: o telefone e o tenant são da conversa, não da escolha.
    """

    model_config = ConfigDict(frozen=True)


class Continuar(BaseModel):
    """Segue no fluxo. Renderizável só com o rascunho — sem I/O novo."""

    model_config = ConfigDict(frozen=True)

    mensagem: MensagemMinhasReservas
    rascunho: Rascunho


class Cancelar(BaseModel):
    """Descritor de I/O: a casca transforma em cancelar_reserva + aviso."""

    model_config = ConfigDict(frozen=True)

    item: ReservaDoMorador


class Encerrar(BaseModel):
    """Sai do fluxo e volta ao menu; o rascunho some."""

    model_config = ConfigDict(frozen=True)

    mensagem: MensagemMinhasReservas | Mensagem


Avanco = MostrarLista | Continuar | Cancelar | Encerrar


def avancar(rascunho: Rascunho | None, escolha: str) -> Avanco:
    """Decide o próximo passo. O que precisa de banco vira descritor.

    `rascunho is None` é a entrada no fluxo — aí `escolha` é ignorada: o "5" veio
    do menu e não escolhe nada aqui dentro.
    """
    if rascunho is None:
        return MostrarLista()

    match rascunho:
        case RascunhoLista():
            return _lista(escolha, rascunho)
        case RascunhoConfirmacao():
            return _confirmacao(escolha, rascunho)


def _lista(escolha: str, rascunho: RascunhoLista) -> Avanco:
    """Escolha inválida remostra a MESMA lista com a MESMA numeração."""
    escolhido = opcao(escolha)
    if escolhido is not None and 1 <= escolhido <= len(rascunho.opcoes):
        return Continuar(
            mensagem=MensagemMinhasReservas.CONFIRMAR,
            rascunho=RascunhoConfirmacao(item=rascunho.opcoes[escolhido - 1]),
        )
    return Continuar(
        mensagem=MensagemMinhasReservas.RESERVA_NAO_ENTENDIDA, rascunho=rascunho
    )


def _confirmacao(escolha: str, rascunho: RascunhoConfirmacao) -> Avanco:
    """O cinto de segurança: a tela recitou área e data por extenso antes disto."""
    match opcao(escolha):
        case OpcaoConfirmacao.SIM:
            return Cancelar(item=rascunho.item)
        case OpcaoConfirmacao.NAO:
            return Encerrar(mensagem=Mensagem.NADA_CANCELADO)
        case _:
            return Continuar(
                mensagem=MensagemMinhasReservas.CONFIRMACAO_NAO_ENTENDIDA,
                rascunho=rascunho,
            )
