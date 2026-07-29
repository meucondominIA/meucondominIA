
"""Motor do wizard de ocorrência (Fase 4 · Etapa 4): função pura, zero I/O.

Espelha reserva.py, com uma diferença que define a etapa: aqui NÃO existe
descritor de leitura. A reserva precisa do MostrarDias porque a tela de datas não
é renderizável sem consultar a agenda; na ocorrência toda tela sai do rascunho e
de constantes deste módulo — os três tipos são fixos, a descrição é o que o
morador acabou de digitar, a confirmação recita as duas coisas.

Por isso `avancar` não recebe nada do banco. Não é elegância: é o que permite
cobrir 100% das transições sem montar uma fixture de dados, e é o que deixa o
processador chamar o motor de novo DEPOIS de subir a foto, já sem conexão na mão.

Também não há congelamento de opções (o RascunhoDia da reserva). Congelar existe
lá porque a lista de dias ENCOLHE entre telas e o número 3 passaria a significar
outra data. Os três tipos não encolhem.

Os bytes da foto nunca entram aqui. O motor vê `tem_foto` (chegou mídia) e, na
segunda passada, o `Anexo` já guardado — quem carrega bytes é a casca. É o que
mantém este módulo barato de importar e de testar.
"""

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from roteador import Mensagem, OpcaoConfirmacao, opcao

MAX_DESCRICAO = 1000
SEGUIR_SEM_FOTO = 1


class TipoSolicitacao(Enum):
    """Enum PURO, não `str, Enum`: o valor 'ocorrencia' colide com Estado.OCORRENCIA
    (==, mesmo hash, match no braço errado — medido). O prefixo que salva
    MensagemReserva não serve aqui: o valor é imposto pelo solicitacoes_tipo_check.

    O CHECK aceita 'outro'; o wizard não o oferece.
    """

    RECLAMACAO = "reclamacao"
    OCORRENCIA = "ocorrencia"
    MANUTENCAO = "manutencao"


_TIPOS = (TipoSolicitacao.RECLAMACAO, TipoSolicitacao.OCORRENCIA,
          TipoSolicitacao.MANUTENCAO)


class Passo(str, Enum):
    """Sub-estados do wizard. Vivem no rascunho, não em conversas.estado — por
    isso acrescentar um passo não exige migration (D1)."""

    TIPO = "tipo"
    DESCRICAO = "descricao"
    FOTO = "foto"
    CONFIRMACAO = "confirmacao"


class MensagemOcorrencia(str, Enum):
    """Identidades que o MOTOR emite. O 0 do roteador emite Mensagem.NADA_REGISTRADO.

    Valores prefixados pela mesma razão de MensagemReserva: `str` enums de valor
    igual são == e têm o mesmo hash, então um valor repetido faria o braço errado
    do match capturar a mensagem.
    """

    ESCOLHER_TIPO = "ocorrencia_escolher_tipo"
    TIPO_NAO_ENTENDIDO = "ocorrencia_tipo_nao_entendido"
    PEDIR_DESCRICAO = "ocorrencia_pedir_descricao"
    DESCRICAO_VAZIA = "ocorrencia_descricao_vazia"
    DESCRICAO_LONGA = "ocorrencia_descricao_longa"
    PEDIR_FOTO = "ocorrencia_pedir_foto"
    FOTO_NAO_ENTENDIDA = "ocorrencia_foto_nao_entendida"
    FOTO_RECUSADA = "ocorrencia_foto_recusada"
    FOTO_FALHOU = "ocorrencia_foto_falhou"
    CONFIRMAR = "ocorrencia_confirmar"
    CONFIRMACAO_NAO_ENTENDIDA = "ocorrencia_confirmacao_nao_entendida"
    REGISTRADA = "ocorrencia_registrada"


class Anexo(BaseModel):
    """A coordenada de um arquivo no Storage — nunca o arquivo, nunca URL assinada
    (ela expira; quem precisar ver assina na hora).

    Mora aqui porque é parte do rascunho persistido, e porque mantém httpx fora do
    grafo de import do motor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    bucket: str
    caminho: str
    mimetype: str
    bytes: int
    sha256: str


class Escolha(BaseModel):
    """O que o morador mandou.

    `texto` é o digitado ou a legenda. `tem_foto` é "chegou foto que AINDA
    PRECISA subir" — marcador, nunca bytes: o motor decide, a casca sobe. `anexo`
    é a segunda passada, com o upload já feito; aí `tem_foto` volta a False,
    porque não há mais o que subir.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    texto: str | None = None
    tem_foto: bool = False
    anexo: Anexo | None = None


class _Rascunho(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RascunhoTipo(_Rascunho):
    """Vazio de propósito: nada foi escolhido. Existe porque o
    chk_conversas_rascunho EXIGE um objeto jsonb no estado do wizard."""

    passo: Literal[Passo.TIPO] = Passo.TIPO


class RascunhoDescricao(_Rascunho):
    """`anexos` já pode estar cheio: foto SEM legenda chega antes do texto."""

    passo: Literal[Passo.DESCRICAO] = Passo.DESCRICAO
    tipo: TipoSolicitacao
    anexos: list[Anexo] = Field(default_factory=list)


class RascunhoFoto(_Rascunho):
    """Só se chega aqui com a descrição pronta E sem nenhuma foto: quem já mandou
    foto junto da descrição não precisa que a gente peça de novo."""

    passo: Literal[Passo.FOTO] = Passo.FOTO
    tipo: TipoSolicitacao
    descricao: str = Field(min_length=1, max_length=MAX_DESCRICAO)


class RascunhoConfirmacao(_Rascunho):
    """O que vai virar linha em solicitacoes, inteiro e conferido.

    `descricao` repete os limites do motor de propósito: aqui eles são INVARIANTE
    (se levantar, é bug nosso ou jsonb corrompido — e a casca reinicia o wizard),
    lá são ENTRADA do morador (e viram tela, não exceção).
    """

    passo: Literal[Passo.CONFIRMACAO] = Passo.CONFIRMACAO
    tipo: TipoSolicitacao
    descricao: str = Field(min_length=1, max_length=MAX_DESCRICAO)
    anexos: list[Anexo] = Field(default_factory=list)


Rascunho = Annotated[
    RascunhoTipo | RascunhoDescricao | RascunhoFoto | RascunhoConfirmacao,
    Field(discriminator="passo"),
]

_RASCUNHO = TypeAdapter(Rascunho)


def ler(bruto: dict[str, Any]) -> Rascunho:
    """jsonb do banco → objeto tipado. Passo desconhecido levanta em vez de virar
    palpite."""
    return _RASCUNHO.validate_python(bruto)


def gravar(rascunho: Rascunho) -> dict[str, Any]:
    """Objeto tipado → jsonb. `mode="json"` é obrigatório: o codec do db.py é
    json.dumps, que não serializa Enum."""
    return rascunho.model_dump(mode="json")


class Continuar(BaseModel):
    """Segue no wizard. Renderizável só com o rascunho — sem I/O nenhum."""

    model_config = ConfigDict(frozen=True)

    mensagem: MensagemOcorrencia
    rascunho: Rascunho


class GuardarFoto(BaseModel):
    """Descritor de I/O: a casca sobe a mídia e chama o motor de novo com o Anexo.

    Não carrega os bytes nem monta o Anexo: o caminho no Storage começa pelo
    condominio_id, que é da CONVERSA e não da escolha — mesma razão pela qual o
    Concluir da reserva não carrega tenant.
    """

    model_config = ConfigDict(frozen=True)

    rascunho: Rascunho


class Concluir(BaseModel):
    """Descritor de I/O: a casca transforma em criar_solicitacao.

    Sem tenant e sem telefone: são da conversa, não da escolha.
    """

    model_config = ConfigDict(frozen=True)

    tipo: TipoSolicitacao
    descricao: str
    anexos: list[Anexo]


class Encerrar(BaseModel):
    """Sai do wizard e volta ao menu; o rascunho some."""

    model_config = ConfigDict(frozen=True)

    mensagem: MensagemOcorrencia | Mensagem


Avanco = Continuar | GuardarFoto | Concluir | Encerrar


def avancar(rascunho: Rascunho | None, escolha: Escolha) -> Avanco:
    """Decide o próximo passo. O que precisa de rede vira descritor.

    `rascunho is None` é a entrada no wizard — garantido pelo
    chk_conversas_rascunho, que só permite rascunho nos estados de wizard. Aí a
    escolha é ignorada: o "3" veio do menu e não escolhe nada aqui dentro.
    """
    if rascunho is None:
        return Continuar(
            mensagem=MensagemOcorrencia.ESCOLHER_TIPO, rascunho=RascunhoTipo()
        )

    match rascunho:
        case RascunhoTipo():
            return _tipo(escolha)
        case RascunhoDescricao():
            return _descricao(escolha, rascunho)
        case RascunhoFoto():
            return _foto(escolha, rascunho)
        case RascunhoConfirmacao():
            return _confirmacao(escolha, rascunho)


def _limpar(texto: str | None) -> str:
    """As duas únicas normalizações da descrição; o resto é literal.

    O strip é o precedente de _duvidas. O NUL não é escolha: `text` não o
    representa e o jsonb o rejeita (datatype-json), então quebraria já ao gravar
    o rascunho.
    """
    return (texto or "").replace("\x00", "").strip()


def _tipo(escolha: Escolha) -> Avanco:
    """Foto neste passo não vira anexo: sem saber o tipo não há rascunho onde
    pendurá-la, e subir arquivo que talvez não seja usado é lixo no Storage."""
    escolhido = opcao(escolha.texto or "")
    if escolhido is None or not 1 <= escolhido <= len(_TIPOS):
        return Continuar(
            mensagem=MensagemOcorrencia.TIPO_NAO_ENTENDIDO, rascunho=RascunhoTipo()
        )
    return Continuar(
        mensagem=MensagemOcorrencia.PEDIR_DESCRICAO,
        rascunho=RascunhoDescricao(tipo=_TIPOS[escolhido - 1]),
    )


def _descricao(escolha: Escolha, rascunho: RascunhoDescricao) -> Avanco:
    """O passo com mais caminhos, porque é onde a foto pode chegar junto.

    Foto sem legenda guarda o anexo e SEGUE pedindo o texto — a ocorrência sem
    descrição não serve ao síndico. Foto com legenda resolve os dois de uma vez e
    pula o passo da foto: quem já mandou não precisa que a gente peça de novo.
    """
    if escolha.tem_foto:
        return GuardarFoto(rascunho=rascunho)

    anexos = rascunho.anexos + ([escolha.anexo] if escolha.anexo else [])
    descricao = _limpar(escolha.texto)

    if not descricao:
        return Continuar(
            mensagem=MensagemOcorrencia.DESCRICAO_VAZIA,
            rascunho=rascunho.model_copy(update={"anexos": anexos}),
        )
    if len(descricao) > MAX_DESCRICAO:
        return Continuar(
            mensagem=MensagemOcorrencia.DESCRICAO_LONGA,
            rascunho=rascunho.model_copy(update={"anexos": anexos}),
        )

    if anexos:
        return Continuar(
            mensagem=MensagemOcorrencia.CONFIRMAR,
            rascunho=RascunhoConfirmacao(
                tipo=rascunho.tipo, descricao=descricao, anexos=anexos
            ),
        )
    return Continuar(
        mensagem=MensagemOcorrencia.PEDIR_FOTO,
        rascunho=RascunhoFoto(tipo=rascunho.tipo, descricao=descricao),
    )


def _foto(escolha: Escolha, rascunho: RascunhoFoto) -> Avanco:
    """Só se chega aqui com descrição pronta e nenhuma foto ainda.

    O "pular" é NÚMERO como tudo neste projeto — o 0 já é o escape para o menu,
    então seguir sem foto é o 1. Legenda aqui é ignorada: a descrição já está
    fechada, e sobrescrevê-la em silêncio apagaria o que o morador escreveu.
    """
    if escolha.tem_foto:
        return GuardarFoto(rascunho=rascunho)

    if escolha.anexo is not None:
        return _confirmar(rascunho, [escolha.anexo])

    if opcao(escolha.texto or "") == SEGUIR_SEM_FOTO:
        return _confirmar(rascunho, [])

    return Continuar(
        mensagem=MensagemOcorrencia.FOTO_NAO_ENTENDIDA, rascunho=rascunho
    )


def _confirmar(rascunho: RascunhoFoto, anexos: list[Anexo]) -> Continuar:
    return Continuar(
        mensagem=MensagemOcorrencia.CONFIRMAR,
        rascunho=RascunhoConfirmacao(
            tipo=rascunho.tipo, descricao=rascunho.descricao, anexos=anexos
        ),
    )


def _confirmacao(escolha: Escolha, rascunho: RascunhoConfirmacao) -> Avanco:
    """O cinto de segurança: a tela recitou tipo, descrição e anexo antes disto.

    O 2 não cancela nada — nada foi gravado em solicitacoes ainda; ele só não
    abre. A foto já subiu ao Storage e fica órfã: é o preço de subir cedo, e o
    caminho é o sha256, então reenviar a mesma imagem converge no mesmo objeto.
    """
    match opcao(escolha.texto or ""):
        case OpcaoConfirmacao.SIM:
            return Concluir(
                tipo=rascunho.tipo,
                descricao=rascunho.descricao,
                anexos=rascunho.anexos,
            )
        case OpcaoConfirmacao.NAO:
            return Encerrar(mensagem=Mensagem.NADA_REGISTRADO)
        case _:
            return Continuar(
                mensagem=MensagemOcorrencia.CONFIRMACAO_NAO_ENTENDIDA,
                rascunho=rascunho,
            )
