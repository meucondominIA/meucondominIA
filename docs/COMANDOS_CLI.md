# Comandos CLI — Fase 2 (RAG)

> Tutorial dos comandos que operam o pipeline de regras: **ingerir** documentos no
> banco, **perguntar** (demo da busca), **avaliar** a qualidade e **verificar** o código.
>
> Rode tudo a partir da raiz do projeto. Atualizado em 20/07/2026.

---

## Antes de começar

- `venv/` instalado (use `venv/bin/python`, não o `python` do sistema).
- `.env` na raiz com `DATABASE_URL` e `OPENAI_API_KEY` reais — falta de campo
  derruba o programa já no `import`, com erro claro.
- **Sempre com `-m`**, da raiz: `python -m ferramentas.ingestao`, nunca
  `python ferramentas/ingestao.py`. As ferramentas importam o core da raiz; pelo
  caminho do arquivo dá `ModuleNotFoundError`.

---

## 1. Ingerir um documento

Carrega um `.txt` de OCR na tabela `regras`: limpa → divide em artigos citáveis →
vetoriza na OpenAI → grava.

```bash
venv/bin/python -m ferramentas.ingestao <ARQUIVO> \
  --documento "<NOME>" (--condominio <SLUG> | --geral) [--dry-run]
```

| Argumento | Obrigatório | O que faz |
|---|---|---|
| `<ARQUIVO>` | sim | caminho do `.txt` de OCR |
| `--documento "<NOME>"` | sim | nome citável (vira a `fonte`) **e** a chave de reingestão |
| `--condominio <SLUG>` | um dos dois | regra de um condomínio específico |
| `--geral` | um dos dois | regra de escopo geral (sem condomínio) |
| `--dry-run` | não | só o relatório; **sem OpenAI e sem banco** |

`--condominio` e `--geral` são mutuamente exclusivos: exatamente um dos dois.

### 1.1 — Sempre comece pelo `--dry-run`

Mostra o que **entraria** no banco sem gastar nada. É onde você pega OCR
mal-extraído antes de pagar embeddings por lixo.

```bash
venv/bin/python -m ferramentas.ingestao "Res.Gabro/CONVEN~1.TXT" \
  --documento "Convenção do Condomínio Edifício Residencial Gabro" \
  --condominio res-gabro --dry-run
```

```
limpeza: 29 página(s) | derrubadas: 1 de assinaturas, 13 de desenho técnico | linhas de cartório removidas: 14
chunks: 57 (56 artigo(s), 1 preâmbulo)
dry-run: nada foi enviado à OpenAI nem gravado no banco.
```

Confira: o número de chunks bate com os artigos do documento, e as páginas
derrubadas são mesmo assinatura/desenho (elas carregam nomes de moradores — LGPD).

### 1.2 — O condomínio precisa existir antes

`--condominio res-gabro` pressupõe o slug na tabela `condominios`. No piloto isso
é SQL manual, uma vez por condomínio:

```sql
insert into condominios (nome, slug)
values ('Edifício Residencial Gabro', 'res-gabro');
```

### 1.3 — Ingestão real

O mesmo comando, sem a flag:

```bash
venv/bin/python -m ferramentas.ingestao "Res.Gabro/CONVEN~1.TXT" \
  --documento "Convenção do Condomínio Edifício Residencial Gabro" --condominio res-gabro
```

```
ingestão concluída (condomínio 'res-gabro'): 57 chunks gravados, 0 antigos substituídos.
```

### 1.4 — Reingestão (corrigiu o OCR)

Rodar com o **mesmo `--documento`** substitui a versão anterior, sem duplicar — o
delete e o insert acontecem na mesma transação, então nunca fica meio documento no
ar. A chave de substituição é o par `(condomínio, documento)`.

> ⚠️ **`0 antigos substituídos` numa reingestão é alarme**, não sucesso: significa
> que o `--documento` saiu diferente e você criou um documento duplicado em vez de
> substituir o antigo.

---

## 2. Perguntar — a demo da busca

Vitrine do retrieval: mesma busca que a Fase 3 vai consumir. Custa 1 embedding
por pergunta.

```bash
venv/bin/python -m ferramentas.perguntar "Posso ter cachorro?" --condominio res-gabro [--k N]
```

```
[0.391] Regimento Interno …, Art. 45
Artigo 45º - É permitido animal doméstico, na unidade residencial obedecido…

[0.519] Regimento Interno …, Art. 46
Artigo 46º - É terminantemente proibido…
```

Ordenado por distância crescente (menor = mais parecido). `--k` sobrepõe o
`rag_top_k` da config (5). Um condomínio nunca vê a regra de outro: o filtro por
tenant está no `WHERE` da query de similaridade.

---

## 3. Avaliar o retrieval

Roda o golden set contra a base real e mede a qualidade. **Custa tokens** e nunca
entra no `pytest`.

```bash
venv/bin/python -m eval.rodar_eval [--golden eval/golden.json] [--k N] [--json saida.json]
```

Imprime `hit@1`, `hit@k`, `completude@k` e vazamentos. **Vazamento > 0 derruba com
exit 1** — é invariante (regra de outro condomínio apareceu), não métrica.

### 3.1 — A sentinela de vazamento (não apague)

`vazamentos 0` só prova algo se existir no banco regra de **outro** condomínio capaz
de aparecer na busca. É esse o papel do condomínio-isca `eval-sentinela`: 4 artigos
falsos (`eval/sentinela.txt`) sobre os mesmos temas do golden — cachorro, silêncio,
salão — com regras contrárias às reais. Se qualquer fonte dele aparecer numa busca do
`res-gabro`, o isolamento entre condomínios vazou.

> ⚠️ **Ele vive no banco de produção de propósito e NÃO deve ser apagado.** Sem a
> isca plantada, o eval segue imprimindo `vazamentos 0` sem ter exercitado
> isolamento nenhum — e a saída fica idêntica à de uma rodada legítima.

Conferir se está plantada (esperado: 4):

```sql
select count(*) from regras r
  join condominios c on c.id = r.condominio_id
 where c.slug = 'eval-sentinela';
```

Se precisar recriá-la (banco novo, restore), é o mesmo fluxo da §1 — o slug primeiro,
a ingestão depois:

```sql
insert into condominios (nome, slug)
values ('Sentinela de Vazamento (eval)', 'eval-sentinela');
```

```bash
venv/bin/python -m ferramentas.ingestao eval/sentinela.txt \
  --documento "Sentinela de Vazamento (eval)" --condominio eval-sentinela
```

---

## 4. Testes e lint (offline, sem custo)

```bash
venv/bin/python -m pytest -q                 # unitários: sem Docker, sem rede
venv/bin/python -m pytest -m integration -q  # integração: sobe Postgres em Docker
venv/bin/ruff check .                        # lint
```

O `pytest` sozinho **exclui** os testes de integração (eles precisam de Docker);
peça com `-m integration`. Sem Docker, são pulados — não falham.

---

## 5. Códigos de saída

| Código | Significa | Exemplos |
|---|---|---|
| **0** | sucesso | — |
| **1** | falha de operação | arquivo não existe · slug não existe no banco · nenhum artigo reconhecível · vazamento no eval |
| **2** | erro de uso | faltou `--condominio`/`--geral` · passou os dois · `--condominio ""` em branco |

A diferença entre **1** e **2**: "slug não existe" só se descobre consultando o
banco (1); "slug em branco" já se vê na linha de comando (2), então é barrado antes
de abrir qualquer conexão. O caso realista de slug em branco é
`--condominio "$SLUG"` com a variável não definida — o shell expande para vazio em
silêncio.

Falha **inesperada** (a OpenAI cai no meio) sobe com traceback e termina não-zero.
Traceback é informação; esconder bug em `except Exception` seria pior.

---

## 6. Mapa rápido

| Arquivo | O que é |
|---|---|
| `ferramentas/ingestao.py` | CLI: carrega documento no banco |
| `ferramentas/perguntar.py` | CLI: demo pergunta → trechos |
| `eval/rodar_eval.py` | CLI: mede a qualidade do retrieval |
| `eval/golden.json` · `eval/sentinela.txt` | dados do eval: gabarito e isca de vazamento (§3.1) |
| `busca.py` | **serviço** (não é CLI): pergunta → embedding → regras; a Fase 3 pluga aqui |
| `limpeza.py` · `chunker.py` · `regras.py` · `condominios.py` · `embeddings.py` · `db.py` | bibliotecas do core, consumidas pelas CLIs e pelo serviço |

O que você **roda à mão** vive em `ferramentas/` e `eval/`; o que o **servidor**
importa fica na raiz. Ambas as pastas são pacotes — por isso o `-m`.
