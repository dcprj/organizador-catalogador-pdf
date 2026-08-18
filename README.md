# Organizador e Catalogador Inteligente de PDFs

Ferramenta de linha de comando que processa lotes de PDFs, converte cada um para
Markdown estruturado, extrai metadados bibliográficos com um **LLM local (via
[Ollama](https://ollama.com))** e organiza tanto os PDFs originais quanto os
Markdowns em uma árvore de diretórios padronizada.

Não usa nenhum provedor pago — tudo roda na sua máquina, sem chave de API, sem
custo por token e sem enviar o conteúdo dos seus PDFs para fora dela.

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

### Opção 1 — Binário standalone (não precisa de Python)

Baixe o executável já pronto para o seu sistema na [página de
Releases](https://github.com/dcprj/organizador-catalogador-pdf/releases) —
`organizador-pdf-macos`, `organizador-pdf-linux` ou
`organizador-pdf-windows.exe`. Dê permissão de execução (macOS/Linux) e rode
direto:

```bash
chmod +x organizador-pdf-macos
./organizador-pdf-macos --help
```

No macOS, o Gatekeeper pode bloquear o primeiro clique duplo por não ser um
binário assinado/notarizado — rode `xattr -d com.apple.quarantine
organizador-pdf-macos` uma vez, ou clique com botão direito → Abrir. O
binário de macOS é gerado só para Apple Silicon (arm64); Macs Intel precisam
da instalação via Python abaixo. Esse binário embute só a CLI em si — o
[Ollama](#configurando-o-ollama) continua sendo instalado à parte.

### Opção 2 — Via Python

Requer **Python 3.10 ou superior** (macOS, Linux e Windows).

```bash
python3.12 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Ou, sem instalar o pacote:

```bash
pip install -r requirements.txt
```

## Configurando o Ollama

**1. Instale o Ollama** (macOS via Homebrew, ou baixe em [ollama.com](https://ollama.com)):

```bash
brew install ollama
brew services start ollama       # ou: ollama serve
```

**2. Baixe o modelo.** Para máquinas com **8 GB de RAM**, o melhor equilíbrio
entre qualidade e memória é o **Qwen2.5 3B Instruct** (~2 GB em disco, bom
suporte a português):

```bash
ollama pull qwen2.5:3b-instruct
```

Se sua máquina tiver bastante mais RAM (16 GB+) e você quiser tentar um modelo
maior, `qwen2.5:7b-instruct` (~4,7 GB) segue instrução um pouco melhor, mas em
8 GB corre risco real de deixar a máquina lenta (troca de memória para disco).
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

## Uso

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
| `--modelo` / `-m`         | `qwen2.5:3b-instruct`     | Modelo do Ollama a usar                                |
| `--ollama-url`            | `http://localhost:11434`  | Endereço do servidor Ollama                            |
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

## Como funciona

Para cada PDF, na ordem:

1. **Conversão** (`converter.py`) — `pymupdf4llm` extrai o texto estruturado em
   Markdown, com o texto simples do PyMuPDF como plano B. PDFs sem texto
   extraível (digitalizados, sem OCR) falham com mensagem explícita.
2. **Extração de metadados** (`extractor.py`) — apenas as **primeiras N
   páginas** vão para o modelo local, que responde em JSON validado contra o
   esquema Pydantic (saída estruturada via `format` do Ollama, não parsing de
   texto livre).
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
pytest              # 90 testes, sem chamadas de rede
```

Estrutura do projeto:

```
src/organizador_pdf/
├── cli.py             # Ponto de entrada e parsing de argumentos (typer)
├── config.py          # Configuração via .env / variáveis de ambiente
├── converter.py       # PDF → Markdown (pymupdf4llm)
├── extractor.py       # Integração com Ollama + esquema JSON + proteções
├── organizer.py       # Sanitização, diretórios, gravação
├── models.py          # Modelos Pydantic dos metadados
├── pipeline.py        # Orquestração resiliente por arquivo
└── logging_utils.py   # Console + erros.log
```

Os testes usam PDFs gerados em tempo de execução e um dublê HTTP do Ollama —
nenhum teste depende de rede nem do Ollama estar rodando.

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
