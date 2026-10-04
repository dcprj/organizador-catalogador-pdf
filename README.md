# Organizador e Catalogador Inteligente de PDFs

Ferramenta de linha de comando de alta precisão que processa lotes de PDFs, extrai metadados bibliográficos estruturados de forma determinística, gera arquivos Markdown companheiros (`.md`) com frontmatter YAML e referências ABNT (NBR 6023:2018), e organiza os arquivos em uma árvore de diretórios padronizada.

A partir da versão **v0.4.0**, o projeto adota um **motor determinístico local e leve**:
- **Zero custo de tokens** e **100% de privacidade**: não depende de LLMs pesados externos (Ollama, OpenAI, Anthropic).
- **Extração de Ficha Catalográfica (CIP)**: identifica e decodifica blocos de catalogação na fonte (AACR2 / ISBD) nas páginas iniciais.
- **Classificador Bibliográfico Especializado**: regras estruturadas para detecção precisa de Livros, Teses/Dissertações/TCCs, Artigos Científicos, Revistas/Periódicos e Apostilas.
- **Validação e Enriquecimento Multi-API Pública**: consulta gratuita contra Brasil API (Câmara Brasileira do Livro / ISBN), Google Books, Crossref (DOI), OpenAlex e OpenLibrary.
- **Formatação ABNT NBR 6023:2018 Estrita**: manipulação correta de sobrenomes compostos e agnomes familiares (Filho, Neto, Júnior, Sobrinho).
- **Markdown Companheiro Inteligente**: amostragem rápida das 10 primeiras e 10 últimas páginas para metadados, sem duplicar o texto integral do PDF.
- **Nomenclatura Visual Padronizada**: renomeação clara no formato `SOBRENOME, Nome - Título (Ano)`.

```
destino/
├── Livros/
│   ├── FRANKL, Viktor E. - Em Busca de Sentido (2019).pdf
│   └── FRANKL, Viktor E. - Em Busca de Sentido (2019).md
├── Artigos Científicos/
│   ├── SILVA, João; SANTOS, Maria - Aprendizado de Máquina em Saúde (2023).pdf
│   └── SILVA, João; SANTOS, Maria - Aprendizado de Máquina em Saúde (2023).md
└── revisao_manual/
    └── ... (arquivos com divergência ou baixa confiança para inspeção humana)
```

O arquivo `.md` companheiro traz frontmatter YAML compatível com o Obsidian, Logseq e Notion, incluindo a referência ABNT formatada e metadados estruturados.

---

## Instalação

Requer **Python 3.10 ou superior** (recomendado Python 3.12).

### Instalação em Modo de Desenvolvimento

```bash
# Clone ou acesse o diretório do repositório
git clone https://github.com/dcprj/organizador-catalogador-pdf.git
cd organizador-catalogador-pdf

# Crie e ative o ambiente virtual
python3 -m venv .venv
source .venv/bin/activate       # No Windows: .venv\Scripts\activate

# Instale o pacote e suas dependências
pip install -e ".[dev]"
```

Também é possível instalar diretamente via `requirements.txt`:
```bash
pip install -r requirements.txt
```

---

## Uso

O projeto oferece duas interfaces de execução complementares:

1. **`organizador-pdf` (ou `python -m organizador_pdf`)**: CLI oficial de alta performance recomendada para produção. Suporta processamento paralelo com múltiplos workers (`--paralelo`), retomada com `--resume`, subpasta customizada para Markdowns (`--subpasta-md`) e organiza os arquivos em uma **árvore taxonômica hierárquica CNPq** (`<destino>/<Grande Área>/<Área>/<Tipo>/`).
2. **`python main.py`**: Ponto de entrada direto para organização em **estrutura plana por categoria** (`<destino>/<Tipo>/`), ideal para organização simplificada de bibliotecas pessoais.

```bash
# Simulação rápida: analisa e exibe a catalogação sem gravar nem mover nada
organizador-pdf --origem ~/Downloads/meus_pdfs --destino ~/Biblioteca --dry-run

# Processamento real (copia os PDFs e gera os Markdowns)
organizador-pdf -i ~/Downloads/meus_pdfs -o ~/Biblioteca

# Move os PDFs em vez de copiar, separando os .md em subpasta espelho
organizador-pdf -i ~/Downloads/meus_pdfs -o ~/Biblioteca --mover --subpasta-md Markdown

# Processamento concorrente para grandes lotes
organizador-pdf -i ~/Downloads/meus_pdfs -o ~/Biblioteca --paralelo 4

# Desativa consultas externas a bases bibliográficas (enriquecimento)
organizador-pdf -i ~/Downloads/meus_pdfs -o ~/Biblioteca --sem-enriquecimento-online

# Retomada automática após interrupção (Ctrl+C ou queda)
organizador-pdf --resume
```

### Opções da CLI

| Opção | Padrão | Descrição |
| :--- | :--- | :--- |
| `--origem` / `--input` / `-i` | *obrigatório* | Diretório de origem contendo os arquivos PDF |
| `--destino` / `--output` / `-o` | *obrigatório* | Diretório raiz de destino da árvore organizada |
| `--dry-run` | `False` | Executa o pipeline sem realizar alterações em disco |
| `--resume` | `False` | Retoma o lote pendente de onde parou |
| `--recursive` / `-r` | `True` | Varredura recursiva em subpastas (`--no-recursive` desativa) |
| `--mover` | `False` | Move o arquivo PDF original em vez de copiar |
| `--subpasta-md` | `None` | Grava os arquivos `.md` em subpasta espelho |
| `--sem-enriquecimento-online` | `False` | Desativa consultas a APIs bibliográficas (mantém metadados locais) |
| `--paralelo` / `-j` | `1` | Número de workers concorrentes para processar o lote |
| `--quarantine` | `True` | Roteia itens de baixa confiança para `revisao_manual/` |
| `--limite` / `-n` | `None` | Limita o número máximo de arquivos processados |
| `--interactive` / `--validate` | `False` | Modo interativo passo a passo com confirmação de metadados |
| `--log` | `erros.log` | Arquivo para registro detalhado de erros |
| `--verbose` / `-v` | `False` | Habilita logs informativos detalhados no console |
| `--version` | — | Exibe a versão instalada da CLI |

---

## Validador Interativo (Diagnostic Tool)

O projeto inclui uma ferramenta interativa no terminal para inspeção passo a passo e diagnósticos de classificação:

```bash
python scripts/interactive_validator.py --origem ~/Downloads/meus_pdfs --destino ~/Biblioteca
```

Recursos do Validador:
1. Inspeção de páginas e camadas de texto nativo.
2. Exibição de candidatos a metadados extraídos.
3. Raciocínio de classificação e pontuação por tipo documental.
4. Consulta ao vivo em APIs externas com visualização de similaridade.
5. Confirmação ou ajuste campo a campo antes da gravação final.

---

## Como Funciona o Pipeline

1. **Amostragem Leve de Texto (`converter.py`)**:
   Extrai o texto nativo das primeiras e últimas 10 páginas via PyMuPDF. PDFs escaneados (sem camada de texto nativa) são reportados para OCR prévio.
2. **Classificação e Heurísticas (`classifier_jev.py`)**:
   - Detecta Fichas Catalográficas (CIP) no padrão AACR2/ISBD.
   - Aplica filtros de disclaimers de repositórios universitários para evitar alucinação de autores institucionais.
   - Identifica elementos estruturais de teses, dissertações, artigos científicos com DOI/ISSN, revistas e apostilas.
3. **Validação e Enriquecimento (`metadata_api.py`)**:
   Se identificadores (ISBN, DOI) ou títulos forem encontrados, consulta bases bibliográficas públicas (Brasil API, Google Books, Crossref, OpenAlex, OpenLibrary) para validar ou preencher campos ausentes (ano, editora, local).
4. **Formatação ABNT (`abnt_formatter.py`)**:
   Gera a referência padronizada conforme as normas da ABNT NBR 6023:2018.
5. **Organização e Gravação Segura (`organizer.py`)**:
   - Cria os diretórios categorizados por tipo.
   - Aplica salvaguarda de comprimento de caminho (truncamento dinâmico para Windows MAX_PATH).
   - Resolve colisões de nomes adicionando sufixos automáticos ` (2)`, ` (3)`.
   - Direciona arquivos com avisos ou baixa confiabilidade para `revisao_manual/`.

---

## Suíte de Testes

Os testes são automatizados via `pytest` e não dependem de chamadas ativas de rede nem de credenciais externas:

```bash
pytest
```

Resultado esperado: **181 testes passando com 100% de sucesso**.

---

## Licença

Distribuído sob os termos da licença MIT. Consulte o arquivo `LICENSE` para mais informações.
