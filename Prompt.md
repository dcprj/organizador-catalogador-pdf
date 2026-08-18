# Projeto: Organizador e Catalogador Inteligente de PDFs (CLI)

Você é um desenvolvedor especialista em Python. Seu objetivo é construir uma ferramenta CLI multiplataforma que processe lotes de arquivos PDF, converta-os para Markdown estruturado, extraia metadados bibliográficos detalhados usando um LLM e organize tanto os PDFs originais quanto os Markdowns em uma árvore de diretórios hierárquica e padronizada.

---

## 1. Requisitos Não Funcionais (RNF)

* **Linguagem & Ambiente:** Python 3.10+ com uso de tipagem estática (`typing`) e `pydantic`.
* **Multiplataforma:** Compatibilidade total com macOS, Linux e Windows (utilizar estritamente `pathlib.Path` para manipulação de caminhos e evitar problemas com separadores de diretório).
* **Interface:** Interface de linha de comando (CLI) simples e intuitiva (via `typer` ou `argparse`).
* **Tratamento de Erros:** Processamento resiliente por arquivo. Falhas em um PDF individual não devem interromper o processamento em lote; erros devem ser logados no terminal e em um arquivo `erros.log`.
* **Custo-eficiência:** A análise via LLM deve consumir apenas as primeiras $N$ páginas do documento (ou metadados textuais extraídos da introdução/ficha catalográfica) para minimizar custos com tokens.

---

## 2. Requisitos Funcionais (RF)

### 2.1. Entrada de Parâmetros (CLI)
A aplicação deve aceitar os seguintes argumentos na linha de comando:
* `--origem` / `-i`: Caminho do diretório contendo os PDFs a serem processados.
* `--destino` / `-o`: Caminho do diretório raiz onde os arquivos organizados serão salvos.
* `--dry-run`: (Opcional) Executar a análise e exibir a estrutura final planejada sem mover ou copiar arquivos.
* `--recursive` / `-r`: (Opcional, padrão `True`) Buscar PDFs em subpastas da origem.

---

### 2.2. Pipeline de Processamento por Arquivo

1. **Conversão PDF para Markdown:**
   * Utilizar bibliotecas Python locais (ex: `pymupdf4llm` ou `pypdf`/`marker`) para extrair o texto estruturado do PDF diretamente para Markdown.

2. **Extração e Análise de Metadados:**
   * Enviar o conteúdo inicial do texto extraído (ficha catalográfica, capa, primeiras páginas) para um modelo LLM leve com saída estruturada (`Pydantic`).
   * **Campos obrigatórios de extração:**
     * `tipo_publicacao`: Classificação estrita (Livro, Artigo, Dissertação/Tese, Apostila, Revista, Capítulo de Livro, Outros).
     * `area_principal`: Área macro do conhecimento (ex: Psicologia, Tecnologia, Filosofia).
     * `subarea`: Especialidade temática (ex: Psicologia Organizacional, Logoterapia, Machine Learning).
     * `titulo`: Título principal do trabalho.
     * `subtitulo`: Subtítulo (se houver).
     * `autores`: Lista de autores (destacando o autor principal).
     * `editora_ou_periodico`: Nome da editora, universidade ou revista acadêmica.
     * `ano`: Ano de publicação.
     * `local`: Cidade/Local de publicação (se disponível).
     * `identificadores`: ISBN, ISSN ou DOI (se disponíveis).
     * `referencia_abnt`: Formatação textual completa da referência bibliográfica seguindo a norma ABNT mais recente (NBR 6023).

3. **Geração do Arquivo Markdown Enriquecido:**
   * O arquivo `.md` resultante deve conter no topo um bloco `YAML Frontmatter` contendo todas as variáveis extraídas (para compatibilidade nativa com Obsidian).
   * Abaixo do frontmatter, incluir uma seção explícita:
     ```markdown
     ## Referência Bibliográfica (ABNT)
     > [Texto da citação ABNT formatada]
     
     ---
     
     ## Conteúdo
     [Texto original convertido do PDF]
     ```

4. **Padronização de Nomenclatura e Armazenamento:**
   * **Nome do arquivo padronizado:**
     `<TIPO> - <TITULO> - <SUBTITULO> - <AUTOR_PRINCIPAL> - <ANO> - <EDITORA>`
     *(Remover caracteres especiais/inválidos para sistemas de arquivos: `\ / : * ? " < > |`)*
   * **Estrutura de Diretórios de Destino:**
     O arquivo PDF e o arquivo Markdown correspondente devem ser gravados em:
     `<DESTINO>/<AREA_PRINCIPAL>/<SUBAREA>/<PLURAL_DO_TIPO>/`
     *Exemplo:* `destino/Psicologia/Psicologia Organizacional/Livros/`
   * Copiar o PDF original renomeado para a pasta correspondente e salvar o `.md` gerado no mesmo local (ou em subpasta espelho configurável).

---

## 3. Diretrizes de Implementação

1. Configure um projeto Python modular (`src/`):
   * `cli.py`: Ponto de entrada e parsing de argumentos.
   * `converter.py`: Extração e conversão de PDF para Markdown.
   * `extractor.py`: Integração com API de LLM e validação com `pydantic`.
   * `organizer.py`: Criação de diretórios, sanitização de strings e cópia de arquivos.
2. Crie um arquivo `requirements.txt` ou `pyproject.toml` contendo as dependências mínimas necessárias.
3. Adicione suporte a variáveis de ambiente (`.env`) para gerenciamento das chaves de API necessárias para a etapa do LLM.
4. Comece implementando o scaffolding do projeto, as classes Pydantic de metadados e os testes com arquivos locais.