# Organizador e Catalogador Inteligente de PDFs

Ferramenta de linha de comando de alta precisão que processa lotes de PDFs, extrai metadados bibliográficos estruturados de forma determinística, gera arquivos Markdown companheiros (`.md`) com frontmatter YAML e referências ABNT (NBR 6023:2018), e organiza os arquivos em uma árvore de diretórios padronizada.

A partir da versão **v0.4.0**, o projeto adota uma arquitetura determinística unificada:
- **Classificador Bibliográfico Jev (Núcleo do Sistema)**:
  - **Modo 'auto' (Padrão)**: utiliza validação semântica TypeSafe AI se `TYPESAFE_API_KEY` estiver configurada e `typesafe-sdk` instalado; caso contrário, executa com heurísticas locais calibradas.
  - **Modo 'local'**: garante operação 100% offline e privada, ultra-rápida, sem consumo de rede ou chaves externas.
  - **Modo 'remoto' / 'jev_remoto'**: modo exclusivo TypeSafe AI remoto. Exige chave e SDK, falhando com erro explícito (`ErroDeConfiguracao` ou `ErroDeClassificacaoRemota`) sem fallback silencioso para heurísticas locais.
  - **Cache Idempotente**: requisições remotas bem-sucedidas são salvas em cache local por fingerprint textual (SHA-256), evitando cobranças ou submissões externas duplicadas após falhas locais de escrita ou retomada.
  - **Vocabulário Ampliado**: suporta Artigos Científicos, Livros, Capítulos de Livros, Teses/Dissertações, Revistas, Apostilas, Relatórios e Trabalhos em Eventos.
- **Validação Rigorosa de Identificadores**:
  - DOIs localizados em seções bibliográficas/referências ou em contextos de citação textual (`in:`, `apud`, etc.) são descartados e não são atribuídos à obra principal.
  - Origem da evidência do DOI (`doi_source`) e do classificador (`provedor_classificador`) são persistidos no frontmatter do Markdown.
- **Enriquecimento Bibliográfico em Bases Públicas (Configurável)**:
  - Consulta bases abertas e gratuitas (Brasil API / CBL para ISBN, Crossref para DOI, OpenAlex, Google Books, OpenLibrary) utilizando apenas identificadores ou título (o arquivo PDF e o texto completo **nunca** são transmitidos).
  - Pode ser completamente desativado definindo `ORGPDF_VERIFICAR_ONLINE=false` no arquivo `.env`.
- **Formatação ABNT NBR 6023:2018 Estrita**: manipulação precisa de sobrenomes compostos, agnomes familiares (Filho, Neto, Júnior, Sobrinho) e instituições.
- **Organização Flexível em Diretórios**:
  - **Hierárquica CNPq (Padrão da CLI)**: `<destino>/<Grande Área>/<Área>/<Tipo>/`
  - **Plana por Categoria (`--plana` ou `--estrutura plana`)**: `<destino>/<Tipo>/`
- **Markdown Companheiro Inteligente**: amostragem rápida das primeiras e últimas páginas para metadados (até 30.000 caracteres por padrão), compatível com Obsidian, Logseq e Notion.
- **Nomenclatura Visual Padronizada**: renomeação uniforme no formato `SOBRENOME, Nome - Título (Ano)`.

```
destino/
├── Livros/
│   ├── FRANKL, Viktor E. - Em Busca de Sentido (2019).pdf
│   └── FRANKL, Viktor E. - Em Busca de Sentido (2019).md
├── Artigos Científicos/
│   ├── SILVA, João; SANTOS, Maria - Aprendizado de Máquina em Saúde (2023).pdf
│   └── SILVA, João; SANTOS, Maria - Aprendizado de Máquina em Saúde (2023).md
└── revisao_manual/
    └── ... (arquivos com possível divergência para conferência)
```

---

## Instalação

Requer **Python 3.10 ou superior** (recomendado Python 3.12).

### Instalação Básica (100% Local / Heurísticas Jev)

```bash
# Clone ou acesse o repositório
git clone https://github.com/dcprj/organizador-catalogador-pdf.git
cd organizador-catalogador-pdf

# Crie e ative o ambiente virtual
python3 -m venv .venv
source .venv/bin/activate       # No Windows: .venv\Scripts\activate

# Instalação padrão
pip install -e .
```

### Instalação com Suporte TypeSafe AI (Modo Remoto Exclusivo)

Para ambientes que utilizam a classificação remota via TypeSafe AI:
```bash
pip install -e ".[typesafe]"
```
Configure a chave de API no ambiente ou arquivo `.env`:
```bash
export TYPESAFE_API_KEY="sua_chave_typesafe"
```
Quando executado com `--classificador remoto` (ou `--classificador jev_remoto`), o sistema opera em modo remoto estrito: se a chave não estiver configurada ou o pacote `typesafe-sdk` não estiver instalado, a execução falhará imediatamente com erro claro (`ErroDeConfiguracao`), sem fallback silencioso para heurísticas locais.

Para desenvolvimento e execução da suíte completa de testes:
```bash
pip install -e ".[dev]"
```

---

## Uso

O comando oficial é **`organizador-pdf`** (ou `python -m organizador_pdf`). O script `python main.py` também está disponível como atalho de compatibilidade, encaminhando para o mesmo pipeline.

### Exemplos Rápidos

```bash
# 1. Simulação (dry-run): analisa os PDFs e exibe a árvore sem alterar disco
organizador-pdf -i ~/Downloads/meus_pdfs -o ~/Biblioteca --dry-run

# 2. Execução padrão (copia arquivos para a árvore hierárquica CNPq)
organizador-pdf -i ~/Downloads/meus_pdfs -o ~/Biblioteca

# 3. Organização em estrutura plana por categoria (<destino>/<Tipo>/)
organizador-pdf -i ~/Downloads/meus_pdfs -o ~/Biblioteca --plana

# 4. Mover arquivos originais em vez de copiar, salvando os .md em subpasta espelho
organizador-pdf -i ~/Downloads/meus_pdfs -o ~/Biblioteca --mover --subpasta-md Markdown

# 5. Processamento paralelo acelerado (múltiplos workers)
organizador-pdf -i ~/Downloads/meus_pdfs -o ~/Biblioteca --paralelo 4

# 6. Modo interativo passo a passo (validação e edição assistida de metadados)
organizador-pdf -i ~/Downloads/meus_pdfs -o ~/Biblioteca --interactive

# 7. Retomada resiliente após interrupção (Ctrl+C ou fechamento do terminal)
organizador-pdf --resume
```

### Opções da Linha de Comando

| Opção | Padrão | Descrição |
| :--- | :--- | :--- |
| `--origem` / `--input` / `-i` | *obrigatório* | Diretório de origem contendo os arquivos PDF |
| `--destino` / `--output` / `-o` | *obrigatório* | Diretório raiz de destino da biblioteca organizada |
| `--estrutura` | `cnpq` | Modelo de diretórios: `cnpq` (árvore taxonômica) ou `plana` (apenas categoria) |
| `--plana` | `False` | Atalho para organizar em estrutura plana (`--estrutura plana`) |
| `--classificador` | `auto` | Modo do classificador: `auto`, `local`, ou `remoto` / `jev_remoto` |
| `--dry-run` | `False` | Executa o pipeline sem realizar alterações em disco |
| `--resume` | `False` | Retoma o lote pendente exatamente de onde parou |
| `--recursive` / `-r` | `True` | Varredura recursiva em subpastas (`--no-recursive` desativa) |
| `--mover` | `False` | Move o arquivo PDF original em vez de copiar (padrão seguro: cópia) |
| `--subpasta-md` | `None` | Grava os arquivos `.md` em subpasta espelho |
| `--paralelo` / `-j` | `1` | Número de workers concorrentes para processar o lote |
| `--quarantine` | `True` | Roteia itens com divergência para `revisao_manual/` |
| `--limite` / `-n` | `None` | Limita a quantidade máxima de arquivos processados no lote |
| `--interactive` / `--validate` | `False` | Modo interativo passo a passo com confirmação de metadados |
| `--log` | `erros.log` | Arquivo para registro detalhado de erros |
| `--verbose` / `-v` | `False` | Habilita logs informativos detalhados no console |
| `--version` | — | Exibe a versão instalada da CLI |

---

## Modo Interativo e Diagnóstico

Ao acionar `--interactive` (ou `--validate`), o sistema apresenta cada PDF passo a passo no terminal:
1. Inspeção de páginas e camada de texto nativo detectada.
2. Exibição dos candidatos preliminares (Título, Autores, Ano, Editora, CIP, ISBN, DOI).
3. Raciocínio detalhado da classificação Jev com pontuações calibradas por categoria.
4. Consulta em tempo real a APIs bibliográficas com indicação de correspondência.
5. Confirmação imediata ou modo de edição manual dos campos antes da gravação.

---

## Suíte de Testes

Os testes são automatizados via `pytest` e operam com dublês de teste, sem depender de rede nem de credenciais externas:

```bash
# Executar todos os testes unitários e de integração
pytest

# Executar com relatório de cobertura detalhado
pytest --cov=organizador_pdf --cov-report=term-missing
```

---

## Licença

Distribuído sob os termos da licença MIT. Consulte o arquivo `LICENSE` para mais detalhes.
