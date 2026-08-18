# Organizador e Catalogador Inteligente de PDFs

Ferramenta de linha de comando que processa lotes de PDFs, converte cada um para
Markdown estruturado, extrai metadados bibliográficos com um LLM e organiza
tanto os PDFs originais quanto os Markdowns em uma árvore de diretórios
padronizada.

Por padrão usa um **LLM local via [Ollama](https://ollama.com)** — sem chave
de API, sem custo por token, nada sai da sua máquina. Se preferir, também dá
para usar um provedor pago (Anthropic, OpenAI, DeepSeek, Gemini ou Grok) com
o modelo de sua escolha — é uma troca explícita, nunca o padrão (veja
[Provedores pagos (opcional)](#provedores-pagos-opcional)).

```
destino/
└── Psicologia/
    └── Logoterapia/
        └── Livros/
            ├── Livro - Em Busca de Sentido - Um psicólogo no campo de concentração - Frankl, Viktor E. - 2019 - Vozes.pdf
            └── Livro - Em Busca de Sentido - Um psicólogo no campo de concentração - Frankl, Viktor E. - 2019 - Vozes.md
```

O `.md` gerado traz um YAML frontmatter compatível com o Obsidian, a referência
ABNT (NBR 6023) em destaque e o texto integral convertido do PDF.

---

## Instalação

Duas peças precisam estar instaladas: **a CLI** (este programa) e o
**[Ollama](https://ollama.com)** (o motor de LLM local que ela chama). Os
binários da CLI ficam na [página de
Releases](https://github.com/dcprj/organizador-catalogador-pdf/releases) —
o repositório é privado, então baixe com o navegador já logado no GitHub, não
por link direto sem autenticação.

Escolha seu sistema:

<details open>
<summary><strong>macOS (Apple Silicon)</strong></summary>

**1. Baixe a CLI** — pegue `organizador-pdf-macos` na página de Releases e:

```bash
chmod +x organizador-pdf-macos
```

O Gatekeeper bloqueia binários não assinados/notarizados baixados pelo
navegador. Se aparecer "não é possível abrir por vir de um desenvolvedor não
identificado", rode uma vez:

```bash
xattr -d com.apple.quarantine organizador-pdf-macos
```

(ou clique com o botão direito no arquivo → Abrir → confirmar). Só existe
binário para **Apple Silicon (arm64)** — em Mac Intel, use a [instalação via
Python](#instalação-via-python-qualquer-sistema) abaixo.

**2. Instale o Ollama e baixe o modelo:**

```bash
brew install ollama
brew services start ollama       # ou: ollama serve
ollama pull qwen2.5:3b-instruct
```

**3. Teste:**

```bash
./organizador-pdf-macos --version
```

</details>

<details>
<summary><strong>Linux</strong></summary>

**1. Baixe a CLI** — pegue `organizador-pdf-linux` na página de Releases e:

```bash
chmod +x organizador-pdf-linux
```

**2. Instale o Ollama e baixe o modelo:**

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:3b-instruct
```

(O instalador já deixa o serviço rodando via systemd na maioria das
distribuições; se não, suba manualmente com `ollama serve &`.)

**3. Teste:**

```bash
./organizador-pdf-linux --version
```

</details>

<details>
<summary><strong>Windows</strong></summary>

**1. Baixe a CLI** — pegue `organizador-pdf-windows.exe` na página de
Releases. O SmartScreen do Windows Defender costuma avisar "o Windows
protegeu o computador" por não ser um binário assinado digitalmente — clique
em **Mais informações → Executar assim mesmo**.

**2. Instale o Ollama** — baixe o instalador em
[ollama.com/download/windows](https://ollama.com/download/windows), rode e,
no PowerShell:

```powershell
ollama pull qwen2.5:3b-instruct
```

**3. Teste** (PowerShell, na pasta onde baixou o arquivo):

```powershell
.\organizador-pdf-windows.exe --version
```

</details>

### Instalação via Python (qualquer sistema)

Alternativa a baixar o binário — útil em Mac Intel, ou se você quiser rodar a
partir do código-fonte. Requer **Python 3.10 ou superior**.

```bash
python3.12 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Ou, sem instalar o pacote:

```bash
pip install -r requirements.txt
```

A partir daqui, o comando é `organizador-pdf` (ou `python -m organizador_pdf`)
em vez do nome do binário — o resto do guia é igual. A instalação do Ollama
segue os mesmos passos de cada sistema acima.

### Sobre o modelo

Para máquinas com **8 GB de RAM**, o melhor equilíbrio entre qualidade e
memória é o **Qwen2.5 3B Instruct** (~2 GB em disco, bom suporte a
português) — é o padrão (`ORGPDF_MODELO`, veja abaixo). Se sua máquina tiver
bastante mais RAM (16 GB+) e você quiser tentar um modelo maior,
`qwen2.5:7b-instruct` (~4,7 GB) segue instrução um pouco melhor, mas em 8 GB
corre risco real de deixar a máquina lenta (troca de memória para disco).
Nos testes deste projeto o ganho de qualidade não compensou o risco — veja
"Limitações conhecidas" abaixo.

**Expectativas realistas:** em CPU (sem GPU dedicada), cada PDF leva de
segundos a cerca de um minuto para processar. É bem mais devagar que uma API
na nuvem — a troca é rodar de graça, offline e sem enviar nada pra fora.

## Configuração (opcional)

```bash
cp .env.example .env
```

Todas as variáveis têm um padrão sensato — o `.env` só é necessário se você
quiser mudar algo:

| Variável                | Padrão                     | Para que serve                          |
| ------------------------ | --------------------------- | ---------------------------------------- |
| `ORGPDF_MODELO`          | `qwen2.5:3b-instruct`       | Modelo do Ollama (precisa já estar baixado) |
| `ORGPDF_OLLAMA_URL`      | `http://localhost:11434`    | Endereço do servidor Ollama              |
| `ORGPDF_MAX_PAGINAS`     | `6`                         | Páginas iniciais enviadas ao modelo      |
| `ORGPDF_MAX_CARACTERES`  | `15000`                     | Teto de caracteres do trecho enviado     |
| `ORGPDF_VERIFICAR_ONLINE` | `true`                     | Verifica ISBN/DOI extraído contra Crossref/Open Library (veja abaixo) |
| `ORGPDF_PROVEDOR`        | `ollama`                    | Provedor do LLM — veja [Provedores pagos (opcional)](#provedores-pagos-opcional) |
| `ORGPDF_API_KEY` / `ORGPDF_<PROVEDOR>_API_KEY` | —      | Chave de API do provedor pago escolhido  |

## Uso

Os exemplos abaixo usam `organizador-pdf` (nome do comando quando instalado
via Python). Se você baixou o binário standalone, troque pelo caminho do
executável — ex.: `./organizador-pdf-macos`, `./organizador-pdf-linux` ou
`.\organizador-pdf-windows.exe` no PowerShell.

```bash
# Simulação: mostra a estrutura planejada sem gravar nada
organizador-pdf --origem ~/Downloads/pdfs --destino ~/Biblioteca --dry-run

# Processamento real (copia os PDFs)
organizador-pdf -i ~/Downloads/pdfs -o ~/Biblioteca

# Move em vez de copiar, e separa os .md em uma subpasta espelho
organizador-pdf -i ~/pdfs -o ~/Biblioteca --mover --subpasta-md Markdown

# Só os PDFs da raiz da origem, limitado a 5 arquivos (bom para um teste rápido)
organizador-pdf -i ~/pdfs -o ~/Biblioteca --no-recursive --limite 5

# Usa um modelo diferente do Ollama (precisa já estar baixado)
organizador-pdf -i ~/pdfs -o ~/Biblioteca --modelo qwen2.5:7b-instruct
```

Também funciona como módulo: `python -m organizador_pdf -i ... -o ...`.

### Opções

| Opção                     | Padrão                  | Descrição                                            |
| ------------------------- | ------------------------ | ----------------------------------------------------- |
| `--origem` / `-i`         | obrigatório               | Diretório com os PDFs a processar                      |
| `--destino` / `-o`        | obrigatório               | Diretório raiz da árvore organizada                    |
| `--dry-run`               | desligado                 | Analisa e exibe o plano sem copiar/mover nada          |
| `--recursive` / `-r`      | ligado                    | Busca em subpastas (`--no-recursive` / `-R` desliga)   |
| `--mover`                 | desligado                 | Move o PDF original em vez de copiá-lo                 |
| `--subpasta-md`           | —                         | Grava os `.md` em uma subpasta espelho                 |
| `--modelo` / `-m`         | `qwen2.5:3b-instruct`     | Modelo a usar (do Ollama, ou do provedor pago escolhido) |
| `--ollama-url`            | `http://localhost:11434`  | Endereço do servidor Ollama                            |
| `--provedor` / `-p`       | `ollama`                 | `ollama`, `anthropic`, `openai`, `deepseek`, `gemini` ou `grok` |
| `--apikey` / `-k`         | —                         | Chave de API do provedor pago (ignorada com `--provedor ollama`) |
| `--limite` / `-n`         | —                         | Processa no máximo N arquivos                          |
| `--log`                   | `erros.log`               | Arquivo de registro de erros                           |
| `--env`                   | `./.env`                  | Caminho de um `.env` alternativo                       |
| `--verbose` / `-v`        | desligado                 | Log detalhado                                          |
| `--version`               | —                         | Mostra a versão e sai                                  |

### Códigos de saída

| Código | Significado                                                     |
| ------ | ---------------------------------------------------------------- |
| `0`    | Todos os arquivos processados com sucesso                        |
| `1`    | Concluído, mas com pelo menos uma falha (ou nenhum PDF achado)   |
| `2`    | Erro de configuração ou lote interrompido (Ollama fora do ar)    |

---

## Provedores pagos (opcional)

O padrão é sempre o Ollama local — nada nesta seção é necessário para usar o
aplicativo. Se você quiser trocar qualidade/velocidade por custo por token
(ex.: PDFs mais difíceis, lotes grandes onde a espera do CPU pesa mais que
alguns centavos), pode apontar a extração para um provedor pago:

```bash
# Anthropic
organizador-pdf -i ~/pdfs -o ~/Biblioteca --provedor anthropic \
  --modelo claude-sonnet-5 --apikey sk-ant-...

# OpenAI, DeepSeek, Gemini e Grok funcionam do mesmo jeito
organizador-pdf -i ~/pdfs -o ~/Biblioteca --provedor openai \
  --modelo gpt-5-mini --apikey sk-...
```

Ou, para não repetir `--apikey` toda vez, no `.env`:

```bash
ORGPDF_PROVEDOR=anthropic
ORGPDF_MODELO=claude-sonnet-5
ORGPDF_ANTHROPIC_API_KEY=sk-ant-...
```

**Como funciona por baixo dos panos:** a Anthropic usa o SDK oficial
(`anthropic`), com saída estruturada validada contra o mesmo esquema Pydantic
usado no Ollama. Os outros quatro (OpenAI, DeepSeek, Gemini e Grok) passam
por um único adaptador que fala o dialeto de API compatível com a OpenAI
(`/chat/completions`) que cada um deles expõe — é por isso que qualquer
modelo desses provedores funciona sem precisar de código novo, só trocando
`--modelo`.

**Ressalvas:**

- Suporte a saída estruturada estrita (JSON Schema) varia entre provedores.
  A validação Pydantic que roda depois da chamada pega qualquer resposta fora
  do formato esperado e transforma em um erro claro para aquele arquivo — o
  lote continua, mas fica pior taxa de sucesso em provedores com suporte mais
  fraco.
- As duas proteções contra alucinação (aviso de divergência de nome de
  arquivo e descarte de identificador não confirmado) valem para **qualquer**
  provedor, pago ou não — um modelo pago também pode alucinar, e revisar os
  itens marcados com `!` continua necessário.
- **Nunca** passe `--apikey` em máquina compartilhada ou script versionado —
  a chave fica visível no histórico do shell e na lista de processos (`ps`).
  Prefira `ORGPDF_<PROVEDOR>_API_KEY` no `.env` (que já está no `.gitignore`).
- Em modo `--verbose`, o log HTTP de debug é silenciado propositalmente
  (`httpx`/`httpcore`) para a chave de API nunca aparecer no console.

---

## Como funciona

Para cada PDF, na ordem:

1. **Conversão** (`converter.py`) — `pymupdf4llm` extrai o texto estruturado em
   Markdown, com o texto simples do PyMuPDF como plano B. PDFs sem texto
   extraível (digitalizados, sem OCR) falham com mensagem explícita.
2. **Extração de metadados** (`extractor.py`/`provedores.py`) — apenas as
   **primeiras N páginas** vão para o LLM escolhido, que responde em JSON
   validado contra o esquema Pydantic (saída estruturada nativa do provedor,
   não parsing de texto livre).
3. **Geração do Markdown** (`organizer.py`) — YAML frontmatter + referência ABNT
   + conteúdo integral.
4. **Organização** (`organizer.py`) — cria
   `<DESTINO>/<ÁREA>/<SUBÁREA>/<TIPO NO PLURAL>/`, sanitiza o nome do arquivo e
   grava o par PDF + Markdown.

### Tratamento de erros

O processamento é resiliente **por arquivo**: um PDF corrompido, protegido por
senha ou sem texto não interrompe o lote — o erro é exibido no terminal, gravado
em `erros.log` e o processamento continua.

A exceção é o Ollama estar fora do ar ou o modelo configurado não estar baixado:
como isso afetaria todo o lote igualmente, o processamento é interrompido
imediatamente em vez de repetir a mesma falha em cada arquivo.

### Nomenclatura e compatibilidade entre sistemas

O nome segue
`<TIPO> - <TÍTULO> - <SUBTÍTULO> - <AUTOR PRINCIPAL> - <ANO> - <EDITORA>`,
omitindo os segmentos sem informação. A sanitização remove `\ / : * ? " < > |`,
converte caracteres de controle em espaço, trata nomes reservados do Windows
(`CON`, `LPT1`…), remove pontos e espaços finais e limita o tamanho dos
segmentos. Acentos são preservados. Colisões recebem sufixo ` (2)`, ` (3)`…,
sempre mantendo o PDF e o `.md` com o mesmo nome.

---

## Limitações conhecidas

Modelos pequenos o bastante para rodar em 8 GB de RAM **alucinam em documentos
sem folha de rosto clara** (capítulos avulsos, PDFs sem capa) — em vez de
admitir incerteza, às vezes inventam uma citação plausível a partir do assunto
do texto. Reforçar o prompt reduz, mas não elimina esse comportamento; é uma
limitação de capacidade do modelo, não algo que se resolve só com instrução.
Confirmado em testes com PDFs reais: um capítulo avulso sem folha de rosto foi
catalogado com autor/título de outra obra citada no corpo do texto — e
corretamente sinalizado com `!` pela proteção abaixo.

Duas proteções determinísticas ficam sempre ativas, independente do modelo,
para reduzir o dano quando isso acontece:

- **Aviso de divergência**: compara o título/autor extraído com o nome do
  arquivo original. Se não houver nenhuma palavra significativa em comum, o
  resultado é marcado com `!` na tabela final e listado em "Possíveis
  divergências" — não bloqueia o processamento, só sinaliza para revisão manual.
- **Identificadores não confirmados**: ISBN, ISSN e DOI só são aceitos se
  aparecerem literalmente no trecho do PDF analisado. Um valor que o modelo
  "lembrou" por conta própria (em vez de ler do documento) é descartado — do
  campo estruturado e também do texto de `referencia_abnt`.

Uma terceira proteção é opcional (`ORGPDF_VERIFICAR_ONLINE`, ligada por
padrão) e é a **única exceção ao funcionamento 100% offline**:

- **Verificação online do identificador**: mesmo um ISBN/DOI confirmado no
  texto do PDF pode pertencer a uma obra diferente da catalogada (ex.: uma
  citação de outro trabalho). O identificador é consultado no Crossref (DOI)
  ou na Open Library (ISBN) — bases públicas, sem chave de API — e o
  título/autor devolvido é comparado com o que foi extraído. Só o código já
  obtido é enviado, nunca o PDF ou o texto extraído. Sem internet ou com as
  APIs fora do ar, a checagem é ignorada em silêncio; para desativar de
  vez, defina `ORGPDF_VERIFICAR_ONLINE=false` no `.env`.

**Sempre revise manualmente os itens marcados com `!`** antes de confiar nos
dados para citar um trabalho. Nenhuma das três proteções garante que o
restante dos metadados (título, autor, editora) esteja correto quando não há
aviso — elas reduzem o risco, não o eliminam.

---

## Desenvolvimento

```bash
pytest              # 129 testes, sem chamadas de rede
```

Estrutura do projeto:

```
src/organizador_pdf/
├── cli.py             # Ponto de entrada e parsing de argumentos (typer)
├── config.py          # Configuração via .env / variáveis de ambiente
├── converter.py       # PDF → Markdown (pymupdf4llm)
├── extractor.py       # Extrator Ollama + prompt + esquema JSON + proteções
├── provedores.py      # Extratores dos provedores pagos (opcionais)
├── verificacao.py     # Verificação online de ISBN/DOI (Crossref/Open Library)
├── organizer.py       # Sanitização, diretórios, gravação
├── models.py          # Modelos Pydantic dos metadados
├── pipeline.py        # Orquestração resiliente por arquivo
└── logging_utils.py   # Console + erros.log
```

Os testes usam PDFs gerados em tempo de execução e dublês HTTP para todo
provedor (Ollama e os pagos) — nenhum teste depende de rede real nem de
credenciais.

### Gerando o binário standalone

```bash
pip install -e ".[build]"
pyinstaller packaging/organizador-pdf.spec
./dist/organizador-pdf --version
```

`packaging/entrypoint.py` existe só para isso: o bootloader do PyInstaller
executa o script como `__main__` solto, sem contexto de pacote, então o
import precisa ser absoluto (`from organizador_pdf.cli import main`) em vez
do relativo usado em `__main__.py` (que é para `python -m organizador_pdf`).

### Publicando um release

Empurrar uma tag `v*` dispara `.github/workflows/release.yml`: roda a
suíte de testes e, se passar, builda o binário em macOS (arm64), Linux e
Windows em paralelo e anexa os três à Release criada automaticamente para a
tag.

```bash
git tag v0.1.0
git push origin v0.1.0
```
