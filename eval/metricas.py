"""Métricas puras do eval (Fase 2 · Passo 7) — sem I/O, sem rede.

hit@1/hit@k respondem "alguma fonte esperada apareceu?"; completude@k, "TODAS
apareceram?" — foi a completude que reprovou k=3 no experimento de 16/07 (as
perguntas multi-artigo perdiam pedaço). Caso com esperadas vazio é pergunta
fora-do-regimento: não pontua hit — existe só para doar a distância do seu
top-1 à distribuição que informará o limiar do "não achei" na Fase 3 (D4).

Vazamento é fonte devolvida fora do conjunto de fontes do próprio tenant.
Não é métrica de qualidade, é invariante: UM vazamento reprova a rodada.
"""

from pydantic import BaseModel, ConfigDict


class ResultadoCaso(BaseModel):
    model_config = ConfigDict(frozen=True)

    pergunta: str
    esperadas: tuple[str, ...]
    obtidas: tuple[str, ...]
    distancias: tuple[float, ...]
    vazamentos: tuple[str, ...]

    @property
    def sem_gabarito(self) -> bool:
        return not self.esperadas

    @property
    def hit_1(self) -> bool:
        return bool(self.obtidas) and self.obtidas[0] in self.esperadas

    @property
    def hit_k(self) -> bool:
        return any(fonte in self.esperadas for fonte in self.obtidas)

    @property
    def completude(self) -> bool:
        return all(fonte in self.obtidas for fonte in self.esperadas)


class Resumo(BaseModel):
    model_config = ConfigDict(frozen=True)

    casos: int
    com_gabarito: int
    hit_1: int
    hit_k: int
    completude: int
    vazamentos: int
    dist_acertos: tuple[float, ...]
    dist_top1_erros: tuple[float, ...]
    dist_sem_gabarito: tuple[float, ...]


def avaliar_caso(
    pergunta: str,
    esperadas: tuple[str, ...],
    obtidas: tuple[str, ...],
    distancias: tuple[float, ...],
    fontes_do_tenant: frozenset[str],
) -> ResultadoCaso:
    """Confronta o que a busca devolveu com o gabarito e o conjunto do tenant."""
    if len(obtidas) != len(distancias):
        raise ValueError(
            f"obtidas e distancias desalinhadas: {len(obtidas)} != {len(distancias)}"
        )
    return ResultadoCaso(
        pergunta=pergunta,
        esperadas=esperadas,
        obtidas=obtidas,
        distancias=distancias,
        vazamentos=tuple(f for f in obtidas if f not in fontes_do_tenant),
    )


def resumir(resultados: list[ResultadoCaso]) -> Resumo:
    """Agrega os casos numa fotografia única da rodada."""
    com_gabarito = [r for r in resultados if not r.sem_gabarito]
    dist_acertos = tuple(
        dist
        for r in com_gabarito
        for fonte, dist in zip(r.obtidas, r.distancias, strict=True)
        if fonte in r.esperadas
    )
    dist_top1_erros = tuple(
        r.distancias[0] for r in com_gabarito if r.obtidas and not r.hit_1
    )
    dist_sem_gabarito = tuple(
        r.distancias[0] for r in resultados if r.sem_gabarito and r.distancias
    )
    return Resumo(
        casos=len(resultados),
        com_gabarito=len(com_gabarito),
        hit_1=sum(r.hit_1 for r in com_gabarito),
        hit_k=sum(r.hit_k for r in com_gabarito),
        completude=sum(r.completude for r in com_gabarito),
        vazamentos=sum(len(r.vazamentos) for r in resultados),
        dist_acertos=dist_acertos,
        dist_top1_erros=dist_top1_erros,
        dist_sem_gabarito=dist_sem_gabarito,
    )
