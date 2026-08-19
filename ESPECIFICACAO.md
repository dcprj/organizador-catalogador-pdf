# Organizador e Catalogador Inteligente de PDFs — Especificação

> Documento de referência do projeto: visão geral, regras de negócio,
> requisitos funcionais e não funcionais, e um prompt de recriação ao final.
> Escrito para sobreviver à perda do repositório — junto com o prompt da
> seção 6, é o suficiente para reconstruir o projeto do zero.

**Versão coberta por este documento:** v0.2.0 (2026-08-18).

---

## 1. Visão geral

CLI multiplataforma (`organizador-pdf`) que processa um lote de PDFs e, para
cada um:

1. converte o conteúdo para Markdown estruturado;
2. extrai metadados bibliográficos via LLM (local por padrão, provedores
   pagos opcionais);
3. gera um `.md` com frontmatter YAML compatível com Obsidian e referência
   ABNT;
4. copia (ou move) o PDF renomeado e grava o `.md` numa árvore de diretórios
   padronizada por área/subárea/tipo.

Filosofia central: **100% local e gratuito por padrão** (Ollama), com
qualquer coisa paga ou que saia da máquina do usuário sendo **opt-in
explícito**. Erros em um arquivo nunca derrubam o lote inteiro. Diante de
incerteza, o app sinaliza em vez de inventar — nunca fabrica uma citação
bibliográfica plausível para preencher lacunas.

---

## 2. Regras de negócio

Estas são as decisões de produto que não são óbvias a partir do código —
o "porquê" por trás do comportamento.

### 2.1. Anti-alucinação é a prioridade nº 1

O risco central do produto é o LLM inventar uma referência bibliográfica
plausível, porém falsa (autor errado, ISBN de outra obra, editora
inexistente) — isso é pior que não extrair nada, porque o usuário confia
nesses dados para citar as obras. Toda a arquitetura gira em torno de
mitigar isso com camadas **determinísticas** (não dependem do LLM se
autocorrigir):

- **Prompt do sistema** instrui explicitamente: identidade da obra (título/
  autor/editora) só pode vir de onde o próprio documento se identifica
  (capa, folha de rosto, cabeçalho, ficha catalográfica) — nunca de uma obra
  citada/discutida no corpo do texto. Preferir campos incertos/`null` a uma
  citação inventada.
- **Descarte de identificadores não confirmados**: um ISBN/ISSN/DOI só
  sobrevive se aparecer literalmente no texto-fonte enviado ao modelo
  (tolerando variação de espaçamento/traço). Se não aparecer, é removido do
  campo estruturado **e** de qualquer menção pontual dentro da
  `referencia_abnt` (regex com fronteira de palavra — sem isso, um código
  fabricado que por coincidência é substring de outro código legítimo
  corromperia o legítimo).
- **Aviso de divergência nome-de-arquivo vs. metadados**: heurística
  (não prova) que compara palavras significativas do nome do arquivo
  original com título/subtítulo/autor/editora extraídos. Pouca ou nenhuma
  palavra em comum é sinal de que o modelo catalogou outra obra citada no
  texto. Não bloqueia o processamento — só sinaliza para revisão manual.
  Nomes de arquivo genéricos ou curtos (< 2 palavras significativas) não
  geram aviso (não há sinal confiável para comparar).
- **Verificação online opcional** (`ORGPDF_VERIFICAR_ONLINE`, ligada por
  padrão): confere o DOI/ISBN extraído contra Crossref/Open Library
  (gratuitas, sem chave). Não corrige nada — só sinaliza divergência.
  Cobertura incompleta (obra não indexada) e falha de rede nunca são
  tratadas como evidência de erro, só como "não deu para confirmar".
- **Fallback para provedor pago é opcional e não é mais confiável por ser
  pago** — as mesmas duas proteções deterministas (descarte de
  identificador, normalização de maiúsculas) rodam para qualquer provedor.

Qualquer arquivo que saia com aviso de **qualquer** uma dessas proteções
(mesmo depois de tentar o fallback) é gravado numa subpasta separada
(`revisao_manual/`) em vez da árvore normal — mesma categorização por área/
subárea/tipo, só fisicamente isolada para facilitar a checagem humana.

### 2.2. Custo e privacidade são o padrão, não a exceção

- O modelo só recebe as **primeiras N páginas** do PDF (padrão 6,
  configurável), não o documento inteiro — é onde ficam capa, folha de
  rosto e ficha catalográfica. Um teto de caracteres adicional
  (padrão 15.000) corta esse trecho antes de enviar.
- O provedor padrão é o Ollama local: sem chave de API, sem custo por
  token, nada sai da máquina do usuário. Qualquer provedor pago (Anthropic,
  OpenAI, DeepSeek, Gemini, Grok) exige escolha explícita
  (`--provedor`/`ORGPDF_PROVEDOR`) e uma chave de API — nunca é o padrão.
- O fallback pago (`--provedor-fallback`) só é acionado quando o resultado
  do provedor principal falha ou sai com aviso — não em toda extração. Sem
  configurá-lo, nenhuma chamada extra é feita.
- A única chamada de rede fora do LLM escolhido é a verificação online
  opcional (Crossref/Open Library) — e ela manda só o identificador (ISBN/
  DOI), nunca o conteúdo do PDF. Pode ser desligada para manter tudo 100%
  offline.
- Chaves de API nunca são persistidas em disco pelo app (nem no arquivo de
  estado do `--resume`) — só existem como variável de ambiente/`.env` ou
  argumento de linha de comando (com aviso de que a flag fica visível no
  histórico do shell).

### 2.3. Resiliência por arquivo, não por lote

- Falha em um PDF (corrompido, sem texto, resposta do LLM fora do esquema)
  não interrompe o lote — é registrada como falha daquele arquivo e o
  processamento segue para o próximo.
- Só uma categoria de erro interrompe o lote inteiro: `ErroFatalDeAPI`
  (credencial inválida, modelo inexistente, servidor fora do ar, limite de
  requisições, saldo insuficiente) — insistir arquivo a arquivo só
  desperdiçaria tempo quando a causa afeta todos.
- Um lote interrompido (Ctrl+C ou erro fatal) pode ser retomado com
  `--resume`, sem repetir nenhum parâmetro da execução original. Um arquivo
  conta como "concluído" (não será retentado) tanto em caso de sucesso
  quanto de falha definitiva — só o que ficou pra trás pela interrupção em
  si é reprocessado. Retentar falhas definitivas automaticamente é uma
  decisão consciente de não fazer: evita reprocessar sem necessidade
  arquivos com problema persistente (ex.: PDF corrompido).

### 2.4. Nomenclatura e organização são determinísticas, não decididas pelo LLM

- O LLM só devolve os *dados*; a nomenclatura de arquivo e a estrutura de
  pastas são montadas por código puro a partir desses dados — reprodutível,
  sem variação de execução para execução.
- Padrão de nome: `<TIPO> - <TÍTULO> - <SUBTÍTULO> - <AUTOR> - <ANO> -
  <EDITORA>`, omitindo segmentos ausentes sem deixar separador órfão.
- Estrutura de pastas: `<DESTINO>/<ÁREA>/<SUBÁREA>/<TIPO NO PLURAL>/` (ou
  `<DESTINO>/revisao_manual/<ÁREA>/<SUBÁREA>/<TIPO NO PLURAL>/` quando há
  aviso) — mesma árvore, isolada só quando necessário.
- Colisão de nome no destino nunca sobrescreve: incrementa sufixo
  ` (2)`, ` (3)`... mantendo PDF e Markdown pareados com o mesmo nome.

### 2.5. Este app não faz OCR

Decisão explícita (revertida de uma tentativa anterior de OCR automático
via Tesseract): PDFs digitalizados/escaneados sem texto extraível **falham
com mensagem explícita**, orientando o uso de um serviço de OCR externo
(ex.: `ocrmypdf`) antes de reprocessar. Motivo: manter o binário leve e sem
dependência de um motor de OCR pesado ou de instalação externa (Tesseract)
como pré-requisito silencioso. `use_ocr=OCRMode.NEVER` é passado
explicitamente na chamada ao `pymupdf4llm` — sem isso, a biblioteca roda
OCR sozinha por padrão quando detecta Tesseract instalado na máquina,
o que tornaria o comportamento dependente do ambiente de quem roda.

---

## 3. Requisitos funcionais (RF)

### RF1 — Entrada de parâmetros (CLI)

Comando único `organizador-pdf` (ou `processar`, via `python -m
organizador_pdf`), com:

| Flag | Curta | Obrigatório | Padrão | Descrição |
|---|---|---|---|---|
| `--origem` | `-i` | sim* | — | Diretório com os PDFs a processar |
| `--destino` | `-o` | sim* | — | Diretório raiz da árvore organizada |
| `--dry-run` | — | não | desligado | Mostra o plano sem gravar nada |
| `--resume` | — | não | desligado | Retoma o último lote interrompido (ver RF8) |
| `--recursive`/`--no-recursive` | `-r`/`-R` | não | ligado | Busca em subpastas |
| `--mover` | — | não | desligado | Move em vez de copiar o PDF original |
| `--subpasta-md` | — | não | — | Grava os `.md` numa subpasta espelho |
| `--modelo` | `-m` | não | `qwen2.5:3b-instruct` | Modelo do provedor escolhido |
| `--ollama-url` | — | não | `http://localhost:11434` | Endereço do servidor Ollama |
| `--provedor` | `-p` | não | `ollama` | `ollama`\|`anthropic`\|`openai`\|`deepseek`\|`gemini`\|`grok` |
| `--apikey` | `-k` | condicional | — | Obrigatório se `--provedor` ≠ `ollama` |
| `--provedor-fallback` | — | não | — | Provedor pago acionado só em falha/aviso |
| `--modelo-fallback` | — | condicional | — | Obrigatório se `--provedor-fallback` usado |
| `--apikey-fallback` | — | condicional | — | Obrigatório se `--provedor-fallback` usado |
| `--max-paginas` | — | não | `6` | Páginas iniciais enviadas ao modelo |
| `--max-caracteres` | — | não | `15000` | Teto de caracteres do trecho enviado |
| `--limite` | `-n` | não | — | Processa no máximo N arquivos |
| `--log` | — | não | `erros.log` | Arquivo de registro de erros |
| `--env` | — | não | `./.env` | Caminho de um `.env` alternativo |
| `--verbose` | `-v` | não | desligado | Log detalhado |
| `--version` | — | não | — | Mostra a versão e sai |

\* `--origem`/`--destino` não são obrigatórios quando `--resume` é usado
(são recuperados do estado salvo).

Toda flag opcional segue a mesma precedência de resolução: **CLI > variável
de ambiente específica (quando existir) > variável de ambiente genérica
(quando existir) > padrão embutido**.

### RF2 — Conversão de PDF para Markdown

- Biblioteca: `pymupdf4llm` (estruturado: títulos, tabelas, ordem de
  leitura), com extração de texto simples via `pymupdf` como plano B se a
  primeira falhar.
- OCR da biblioteca explicitamente desligado (`OCRMode.NEVER`) — ver regra
  de negócio 2.5. PDF sem texto extraível falha com mensagem clara.
- Metadados embutidos no PDF (title/author/subject/keywords/creator/
  producer) são coletados e repassados como pista adicional ao LLM.
- Gera dois textos: o Markdown completo (vai para o `.md` final) e um
  recorte das primeiras N páginas, truncado no teto de caracteres (o único
  trecho de fato enviado ao LLM).

### RF3 — Extração de metadados via LLM

Campos extraídos, validados por um schema Pydantic (`Metadados`):

- `tipo_publicacao`: enum estrito — Livro, Artigo, Dissertação/Tese,
  Apostila, Revista, Capítulo de Livro, Outros.
- `area_principal`: área macro do conhecimento, português, singular,
  capitalizada.
- `subarea`: especialidade temática; repete a área se não identificável.
- `titulo` / `subtitulo`: campos separados.
- `autores`: lista, formato "Sobrenome, Nome"; `autor_principal` deve ser
  um dos itens da lista.
- `editora_ou_periodico`, `ano`, `local`: opcionais, `null` se não
  identificáveis.
- `identificadores`: objeto `{isbn, issn, doi}`, todos opcionais.
- `referencia_abnt`: referência completa segundo NBR 6023, uma linha,
  sobrenome em maiúsculas, usando `s.l.`/`s.n.`/`s.d.` para lacunas.

Saída estruturada nativa do provedor (JSON Schema restringindo a geração,
não parsing de texto livre) sempre que o provedor suportar; DeepSeek é
exceção conhecida (usa `json_object` + instrução textual, por não aceitar
`json_schema` estrito).

Duas pós-processagens deterministas rodam sobre a saída do LLM, para
**qualquer** provedor: normalização de maiúsculas do sobrenome na
referência ABNT, e descarte de identificadores não confirmados no texto-
fonte (ver 2.1).

### RF4 — Provedores de LLM

- **Ollama** (padrão): API `/api/chat`, saída restringida via `format`
  (JSON Schema).
- **Anthropic**: SDK oficial, `messages.parse` com `output_format=Metadados`.
- **OpenAI, DeepSeek, Gemini, Grok**: um único adaptador compatível com a
  API de chat completions da OpenAI (`/chat/completions`), com URL base por
  provedor. Gemini via camada de compatibilidade OpenAI da própria Google.
- Erros são classificados em dois níveis: `ErroDeExtracao` (falha pontual
  daquele arquivo — resposta malformada, timeout, erro 5xx) e
  `ErroFatalDeAPI` (afeta o lote inteiro — credencial inválida, 401/403,
  modelo/404, limite de requisições/429, sem crédito).

### RF5 — Fallback para provedor pago

- Acionado (se `--provedor-fallback` configurado) quando: (a) a extração
  principal falha com `ErroDeExtracao`, ou (b) a extração principal
  sucede mas gera aviso de divergência nome-arquivo/metadados.
- Se o fallback também falhar quando acionado por (a): falha do arquivo,
  reportando os dois erros. Se falhar quando acionado por (b): mantém o
  resultado local (com o aviso original).
- Uso do fallback é registrado (não é, por si, motivo de aviso):
  marcador `$` na tabela do terminal; campos `provedor_extracao`/
  `extraido_via_fallback` no frontmatter do `.md`; contagem "extraído(s)
  localmente vs. via provedor pago" no resumo final do lote.

### RF6 — Geração do Markdown

- Frontmatter YAML no topo: todos os campos de `Metadados`, mais tags no
  formato hierárquico do Obsidian (`area/x`, `subarea/y`, `tipo/z`),
  `arquivo_origem`, `total_paginas`, `catalogado_em` (data ISO),
  `provedor_extracao`, `extraido_via_fallback`.
- Corpo: título (`#`), seção "Referência Bibliográfica (ABNT)" com a
  referência em bloco de citação, separador, seção "Conteúdo" com o
  Markdown completo do documento.

### RF7 — Organização e nomenclatura

- Sanitização de nome remove caracteres inválidos multiplataforma
  (`\ / : * ? " < > |`), colapsa espaços/controles, remove ponto/espaço
  final (regra do Windows), prefixa nomes reservados do Windows (CON, PRN,
  AUX, NUL, COM1-9, LPT1-9), trunca por segmento (120 car.) e no total
  (180 car.), preservando acentos (NFC).
- Nome do arquivo: `<TIPO> - <TÍTULO> - <SUBTÍTULO> - <AUTOR> - <ANO> -
  <EDITORA>`, sem segmentos ausentes.
- Diretório: `<DESTINO>/<ÁREA>/<SUBÁREA>/<TIPO PLURAL>/`, ou com prefixo
  `revisao_manual/` quando há aviso. `.md` pode ir para subpasta espelho
  (`--subpasta-md`).
- Colisão de nome: sufixo ` (2)`, ` (3)`... mantendo PDF/Markdown parelhos.
- `--dry-run`: calcula e mostra tudo sem gravar (nem criar diretório).
- `--mover` vs. copiar (padrão): copiar preserva o PDF original na origem.

### RF8 — Retomada de lote interrompido (`--resume`)

- Cada execução (fora de `--dry-run`) grava, incrementalmente, um arquivo
  de estado global (`~/.organizador-pdf/estado.json`) com os parâmetros da
  chamada (exceto chaves de API) e o caminho absoluto de cada PDF já
  concluído (sucesso ou falha definitiva).
- `--resume`: carrega esse estado, reaplica todos os parâmetros da
  execução original (ignorando quaisquer outras flags passadas junto,
  exceto `--log`/`--env`/`--verbose`/`--limite`, que são meta-parâmetros
  aplicados normalmente), filtra da lista de PDFs os já concluídos, e
  segue o lote.
- Sem estado salvo, `--resume` falha com mensagem clara (nada para
  retomar).
- Ao concluir um lote inteiro sem interrupção, o estado é apagado — o
  próximo `--resume` (sem lote pendente) avisa e não faz nada.

### RF9 — Verificação online opcional

- Para cada identificador já confirmado no texto-fonte (ISBN/DOI), consulta
  Crossref (DOI) ou Open Library (ISBN) e compara título/autor devolvidos
  com o extraído (por sobreposição de palavras significativas, tolerante a
  ausência de dado).
- Diverge → aviso (mesmo tratamento de qualquer outro aviso: vai para
  `revisao_manual/`). Não indexado, sem rede, ou API fora do ar → ignorado
  silenciosamente, sem afetar o restante do processamento.
- Desligável via `ORGPDF_VERIFICAR_ONLINE=false`.

### RF10 — Relatório do lote

Ao final (ou quando interrompido), a CLI mostra: árvore dos arquivos
organizados/planejados; tabela de metadados extraídos (com marcadores `!`
para aviso e `$` para fallback usado); tabela de avisos para revisão;
tabela de falhas com etapa e mensagem; painel de resumo com contagem de
gravados/simulados/falhas, contagem local-vs-pago, e caminho do arquivo de
log.

---

## 4. Requisitos não funcionais (RNF)

- **RNF1 — Linguagem/tipagem:** Python 3.10+, tipagem estática
  (`typing`/`from __future__ import annotations`), modelos de dados via
  `pydantic` (validação de LLM) e `dataclasses` (configuração/estado
  internos).
- **RNF2 — Multiplataforma:** macOS, Linux, Windows. `pathlib.Path` em
  toda manipulação de caminho; sanitização de nome cobre as regras mais
  restritivas dos três sistemas de arquivos.
- **RNF3 — Interface:** CLI via `typer`, com ajuda automática (`--help`),
  saída colorida/tabelas via `rich`.
- **RNF4 — Resiliência:** falha em um PDF nunca interrompe o lote (exceto
  `ErroFatalDeAPI`); todo erro tratado tem mensagem acionável, não só
  stack trace. Log em arquivo (`--log`, padrão `erros.log`) além do
  console.
- **RNF5 — Custo-eficiência:** só as primeiras N páginas (configurável) e
  um teto de caracteres vão ao LLM — nunca o documento inteiro.
- **RNF6 — Privacidade/offline por padrão:** zero chamadas de rede além do
  provedor de LLM escolhido e (opcional) verificação Crossref/Open
  Library — ambas desligáveis para operação 100% offline com Ollama.
- **RNF7 — Segurança de credenciais:** chaves de API nunca são logadas,
  nunca persistidas em disco pelo app; flags de chave alertam sobre
  exposição em histórico de shell; log HTTP de debug silenciado
  (`httpx`/`httpcore`) mesmo em `--verbose`, para nunca vazar
  `Authorization` header.
- **RNF8 — Testabilidade:** suíte `pytest` sem chamadas de rede reais
  (providers e verificação online mockados via `httpx.Client` injetável);
  183 testes na v0.2.0.
- **RNF9 — Distribuição:** pacote Python instalável (`pip install -e .`)
  e binário standalone via PyInstaller (macOS arm64, Linux, Windows),
  publicado automaticamente no GitHub Actions a cada tag `v*`. Arquivos de
  dados carregados em runtime pelo `pymupdf`/`pymupdf4llm` (modelos ONNX do
  motor de layout) são coletados explicitamente no `.spec`
  (`collect_data_files`) — a análise estática do PyInstaller não os
  rastreia sozinha.
- **RNF10 — Idioma:** toda saída de usuário (CLI, mensagens de erro,
  documentação) em português do Brasil; código-fonte (identificadores,
  comentários) também em português.
- **RNF11 — Convenção de branches:** desenvolvimento em `develop`; merge
  para `main` + tag `vX.Y.Z` (SemVer) + `CHANGELOG.md` atualizado a cada
  release validado.

---

## 5. Estrutura do código-fonte

```
src/organizador_pdf/
  cli.py          # ponto de entrada (typer): parsing, --resume, relatório
  config.py       # Config (dataclass) + resolução CLI>env>padrão
  converter.py    # PDF -> Markdown (pymupdf4llm/pymupdf), sem OCR
  models.py       # Metadados, TipoPublicacao (Pydantic)
  extractor.py    # ExtratorOllama + prompt de sistema + pós-processamento
                   # anti-alucinação (usado por todo provedor)
  provedores.py   # ExtratorAnthropic, ExtratorOpenAICompativel, fábricas
  pipeline.py     # orquestra converter->extrair->markdown->organizar,
                   # fallback, aviso de divergência nome/metadados
  organizer.py    # sanitização, nomenclatura, YAML frontmatter, gravação
  verificacao.py  # verificação online DOI/ISBN (Crossref/Open Library)
  estado.py       # persistência do progresso para --resume
  logging_utils.py

packaging/organizador-pdf.spec   # build PyInstaller
.github/workflows/release.yml    # CI: testa, builda 3 binários, publica
tests/                           # pytest, um arquivo por módulo
```

Fluxo de dependência: `cli.py` → `pipeline.py` → (`converter.py`,
`provedores.py`/`extractor.py`, `verificacao.py`, `organizer.py`).
`config.py` e `estado.py` são transversais.

---

## 6. Prompt de recriação

> Copie o bloco abaixo para recriar o projeto do zero com um LLM
> assistente de código, caso o repositório seja perdido. Ele descreve o
> estado da v0.2.0 — mais enxuto que as seções 2–4 acima (que continuam
> sendo a referência completa para tirar dúvidas durante a reconstrução).

```
Você é um desenvolvedor especialista em Python. Construa uma ferramenta CLI
multiplataforma (macOS/Linux/Windows) chamada "organizador-pdf" que processa
um lote de PDFs e, para cada um: converte para Markdown estruturado, extrai
metadados bibliográficos via LLM, gera um .md com frontmatter YAML e
referência ABNT, e organiza PDF+Markdown numa árvore de diretórios por
área/subárea/tipo. Interface e todo texto de usuário em português do
Brasil. Filosofia: 100% local e gratuito por padrão (LLM via Ollama), tudo
que é pago ou sai da máquina é opt-in explícito. Resiliente por arquivo
(falha em um PDF não derruba o lote). Prioridade nº1: nunca fabricar uma
citação bibliográfica plausível, porém falsa — na dúvida, sinalizar em vez
de inventar.

REQUISITOS NÃO FUNCIONAIS
- Python 3.10+, tipagem estática, Pydantic para validação de dados do LLM.
- pathlib.Path em toda manipulação de caminho.
- CLI via typer, saída via rich (tabelas, árvore, cores).
- Log em console + arquivo (erros.log por padrão).
- Só as primeiras N páginas (padrão 6) e um teto de caracteres (padrão
  15000) vão ao LLM — nunca o documento inteiro.
- Zero chamadas de rede além do LLM escolhido e uma verificação
  bibliográfica opcional (Crossref/Open Library, sem chave) — ambas
  desligáveis.
- Chaves de API nunca logadas nem persistidas em disco pelo app.
- Testes (pytest) sem chamadas de rede reais.
- Empacotável como binário standalone (PyInstaller) para as 3 plataformas.

PARÂMETROS DA CLI (precedência: flag > env var específica > env var
genérica > padrão)
--origem/-i e --destino/-o (obrigatórios, exceto com --resume)
--dry-run (mostra plano sem gravar)
--resume (retoma lote interrompido — ver seção RESUME)
--recursive/--no-recursive (-r/-R, padrão ligado)
--mover (padrão: copiar)
--subpasta-md (subpasta espelho pro .md)
--modelo/-m, --ollama-url
--provedor/-p (ollama padrão; anthropic/openai/deepseek/gemini/grok pagos,
  exigem --apikey/-k)
--provedor-fallback/--modelo-fallback/--apikey-fallback (pago, acionado só
  em falha ou aviso do principal; desligado por padrão)
--max-paginas (padrão 6), --max-caracteres (padrão 15000)
--limite/-n, --log, --env, --verbose/-v, --version

PIPELINE POR ARQUIVO
1. Converter PDF->Markdown (biblioteca tipo pymupdf4llm; texto simples via
   pymupdf como plano B). Sem OCR — PDF sem texto extraível falha com
   mensagem clara orientando usar um serviço de OCR externo antes de
   reprocessar. Recolhe metadados embutidos do PDF (title/author/etc.) como
   pista extra.
2. Extrair metadados: envia o trecho inicial (capa/ficha catalográfica) a
   um LLM com saída estruturada (JSON Schema restringindo a geração, não
   parsing de texto livre), validada por um schema Pydantic com estes
   campos:
   - tipo_publicacao: enum (Livro, Artigo, Dissertação/Tese, Apostila,
     Revista, Capítulo de Livro, Outros)
   - area_principal, subarea (português, capitalizado)
   - titulo, subtitulo (separados)
   - autores (lista, "Sobrenome, Nome"), autor_principal
   - editora_ou_periodico, ano, local (opcionais)
   - identificadores: {isbn, issn, doi} (opcionais)
   - referencia_abnt (NBR 6023 completa, uma linha, sobrenome maiúsculo,
     s.l./s.n./s.d. para lacunas)
   Prompt de sistema deve instruir explicitamente: identidade da obra só
   vem de onde o documento se autoidentifica (capa/folha de rosto/
   cabeçalho/ficha catalográfica), nunca de obra citada no corpo do texto;
   na dúvida, campos incertos/null em vez de inventar; nomes de autor
   sempre "Sobrenome, Nome" (tradutor/revisor/prefaciador não são autores).
3. Pós-processar (roda para QUALQUER provedor, determinístico, não
   depende do LLM se autocorrigir):
   a. Forçar sobrenome em maiúsculas dentro de referencia_abnt (comparando
      com os nomes já validados em autores).
   b. Descartar isbn/issn/doi que não aparecem literalmente no texto-fonte
      enviado (tolerando variação de espaço/traço, exigindo fronteira de
      palavra para não corromper um código legítimo que contenha outro
      como substring) — e remover a menção correspondente dentro do texto
      livre de referencia_abnt também.
4. Gerar aviso de divergência: comparar palavras significativas (>=4
   letras, sem stopwords) do nome do arquivo original com
   titulo+subtitulo+autor+editora extraídos. Nome com conteúdo real (>=2
   palavras significativas) e pouca/nenhuma palavra em comum -> aviso
   (heurística, não bloqueia).
5. Se --provedor-fallback configurado: acionar quando a extração principal
   falhar OU gerar o aviso do passo 4. Resultado final é o do fallback se
   ele suceder; se falhar, mantém o local. Registrar (não é aviso por si
   só) qual provedor de fato produziu o resultado — no relatório do
   terminal e no frontmatter do .md.
6. Verificação online opcional (Crossref para DOI, Open Library para
   ISBN): comparar título/autor devolvidos com o extraído por sobreposição
   de palavras; divergência vira aviso; não indexado ou erro de rede é
   ignorado em silêncio.
7. Gerar o .md: frontmatter YAML com todos os campos + tags hierárquicas
   estilo Obsidian (area/x, subarea/y, tipo/z) + arquivo_origem +
   total_paginas + data de catalogação + provedor usado; corpo com título,
   seção de referência ABNT em blockquote, e o Markdown completo do PDF.
8. Organizar: nome de arquivo "<TIPO> - <TITULO> - <SUBTITULO> - <AUTOR> -
   <ANO> - <EDITORA>" (sanitizado: remove caracteres inválidos de
   Windows/macOS/Linux, nomes reservados do Windows, colisão de nome vira
   sufixo " (2)"). Diretório destino: <DESTINO>/<AREA>/<SUBAREA>/<TIPO NO
   PLURAL>/, ou <DESTINO>/revisao_manual/<...> quando há QUALQUER aviso
   (das etapas 4, 5 ou 6) — mesma árvore, só isolada.

RETOMADA DE LOTE (--resume)
Salvar incrementalmente (a cada arquivo concluído, sucesso ou falha
definitiva) um arquivo de estado global fora do repo (ex.:
~/.organizador-pdf/estado.json) com os parâmetros da execução (exceto
chaves de API) e os caminhos já concluídos. --resume carrega esse estado,
reaplica os parâmetros automaticamente (sem exigir repetir --origem/
--destino/etc.), pula os já concluídos, e retoma só o que ficou pra trás
pela interrupção. Falha definitiva conta como concluída (não é retentada
automaticamente). Ao concluir o lote inteiro sem interrupção, apagar o
estado.

ESTRUTURA DE CÓDIGO SUGERIDA (src/organizador_pdf/)
cli.py (typer, parsing + relatório rich), config.py (dataclass Config +
resolução de precedência), converter.py, models.py (Pydantic), extractor.py
(Ollama + prompt de sistema + pós-processamento anti-alucinação),
provedores.py (Anthropic + adaptador OpenAI-compatível para OpenAI/
DeepSeek/Gemini/Grok + fábrica), pipeline.py (orquestração + fallback +
aviso), organizer.py (sanitização + nomenclatura + gravação),
verificacao.py, estado.py.

Comece pelo scaffolding, os modelos Pydantic e testes com PDFs locais
gerados em memória (ex.: via pymupdf), mockando toda chamada de LLM/rede
nos testes automatizados.
```

---

## 7. Como manter este documento

Atualize as seções 2–4 sempre que uma regra de negócio, requisito ou
parâmetro mudar de verdade (não a cada detalhe de implementação) — e
mantenha o prompt da seção 6 coerente com elas. `CHANGELOG.md` é a fonte
de verdade cronológica; este documento é a fonte de verdade do estado
atual.
