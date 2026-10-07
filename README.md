# Organizador e Catalogador Inteligente de PDFs

Ferramenta de linha de comando de alta precisão que processa lotes de PDFs, extrai metadados bibliográficos estruturados de forma determinística, realiza buscas bibliográficas progressivas e econômicas por tipologia, gera arquivos Markdown companheiros (`.md`) com frontmatter YAML e referências ABNT (NBR 6023:2018), e organiza os arquivos em uma árvore de diretórios padronizada.

---

## 🎯 Escopo da Aplicação

- **Tipologias Atendidas**: Livros, artigos científicos, apostilas, teses, dissertações e relatórios técnicos.
- **Proteção e Limite de Escopo**: A aplicação **não trata dados sensíveis**. Documentos de natureza clínica, confidencial ou com dados pessoais sensíveis não são manipulados pelo sistema. A rotina legada `is_sensitive_clinical_document` foi permanentemente removida da base de código; a delimitação é estabelecida na fronteira operacional da biblioteca (o operador deve alimentar exclusivamente documentos de acervo acadêmico e editorial).
- **Transparência de Auditoria**: Cada PDF processado gera um arquivo `<nome>.auditoria.json` documentando fontes consultadas, tempos, status por provedor, valores originais vs. selecionados, e as decisões bibliográficas fundamentadas com rastreabilidade de evidência.

---

## 🏗️ Arquitetura e Decisões Técnicas

### 1. Classificador Bibliográfico Jev (TypeSafe AI) — Padrão Remoto Uniforme
- **Modo Padrão (`jev_remoto`) Unificado**: O padrão é **remoto** em todos os pontos de entrada do sistema (`Config`, `Pipeline`, `PipelineOrganizer`, validador interativo e CLI).
  - **Com chave configurada (`TYPESAFE_API_KEY`)**: Efetua chamada remota estrita à API da TypeSafe AI / Jev.
  - **Sem chave configurada**: `Config.validar()` e `JevClassifier` levantam explicitamente `ErroDeConfiguracao` impedindo qualquer fallback silencioso.
  - **Com SDK ausente (`typesafe-sdk`)**: Levanta `ErroDeClassificacaoRemota` orientando a instalação via `pip install -e ".[typesafe]"`.
  - **Amostragem Mínima Controlada**: Transmite estritamente até **3.000 caracteres** de texto nativo dos metadados de abertura. O arquivo PDF integral **nunca** é transmitido pela rede.
  - **Chamada Fresca por PDF (Sem Cache JEV)**: Não há reaproveitamento de cache local de respostas JEV; cada documento gera uma classificação remota limpa.
- **Modo Local (`local`)**: Executa motor heurístico calibrado determinístico para operação 100% offline, exigindo seleção deliberada via `--classificador local` ou `ORGPDF_CLASSIFICADOR=local`.

### 2. Recuperação de Informação e Biblioteconomia

- **Checksums Estritos**:
  - Validação matemática de ISBN-10 (módulo 11) e ISBN-13 (módulo 10). Candidatos estruturais são preservados na extração, mas validação e enriquecimento exigem conformidade estrita.
  - Validação matemática de ISSN-8 (módulo 11, suportando dígito de controle `X`).
- **Diferenciação em 3 Níveis de Identificadores**:
  1. *Extraído*: padrão textual localizado no PDF.
  2. *Validado*: dígito verificador matemático confirmado.
  3. *Confirmado para a Obra*: verificado junto a registro oficial externo ou fichas catalográficas da mesma edição.
- **Plausibilidade Local vs. Corroboração por Fonte**:
  - `identidade_plausivel_local`: Título consistente e autoria válida extraídos diretamente da estrutura do PDF (sem validação externa).
  - `identidade_corroborada_fonte`: Identidade atestada por autoridade bibliográfica externa (CBL, Crossref, DataCite, Google Books) ou por Ficha Catalográfica formal (CIP/AACR2).
  - `evidencia_identidade`: Campo textual que explicita qual base ou documento sustentou a decisão de identidade.
- **Critério Rigoroso de Busca Externa Esgotada**:
  - `busca_externa_esgotada` é `true` **exclusivamente** quando a busca online estava ativa, fontes pertinentes foram efetivamente consultadas e não restaram erros técnicos de conexão.
  - Se nenhuma fonte foi consultada (ex.: documento sem identificador e sem título válido para consulta), a busca é marcada como `incompleta_sem_consultas` e `busca_externa_esgotada: false`.
- **Rastreabilidade de Cache de APIs Bibliográficas**:
  - Respostas obtidas do cache bibliográfico em disco são obrigatoriamente registradas com `cache_hit: true` e `origem_resposta: "cache_disco"`, distinguindo-as de chamadas remotas ao vivo (`origem_resposta: "rede_remota"`).
  - O cache pode ser totalmente desativado definindo `ORGPDF_CACHE_BIBLIOGRAFICO=false` (ou `cache_bibliografico=False` em `Config`).

### 3. Matriz de APIs Bibliográficas Externas (Limites Oficiais)

| Fonte | Campos Principais | Autenticação / Requisitos | Limites / Custos Oficiais | Documentação Oficial |
| :--- | :--- | :--- | :--- | :--- |
| **TypeSafe AI (Jev)** | Tipologia, Área/Subárea CNPq | `TYPESAFE_API_KEY` | Amostra até 3.000 chars | [TypeSafe AI](https://typesafe.ai) |
| **Crossref** | DOI, Artigo, Autores, Periódico, ISSN | Gratuito (Email em User-Agent) | Rate limits dinâmicos via headers (`X-Rate-Limit-Limit`) | [Crossref API Docs](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/) |
| **DataCite** | DOI, Teses, Repositórios acadêmicos | Gratuito / Público | API REST aberta e gratuita | [DataCite API](https://support.datacite.org/docs/api) |
| **Brasil API / CBL** | ISBN, Livros nacionais, Editora, Ano | Gratuito / Sem chave | Acesso público | [Brasil API](https://brasilapi.com.br/) |
| **OpenLibrary** | ISBN, Livros internacionais, Local | Gratuito / Sem chave | Sem autenticação | [OpenLibrary Developer](https://openlibrary.org/developers/api) |
| **Google Books** | ISBN, Título, Autores, Categorias | Chave opcional (`GOOGLE_BOOKS_API_KEY`) | ~1.000 req/dia livre | [Google Books API](https://developers.google.com/books) |
| **OpenAlex** | Artigos, Teses, Periódicos por título | Gratuito (Email em User-Agent) | Cortesia diária de até $1/dia (taxa de requisições de cortesia) | [OpenAlex Pricing](https://help.openalex.org/access/pricing/) |
| **SerpApi** | Google Scholar fallback por título | `SERPAPI_API_KEY` | 250 buscas/mês no plano Free | [SerpApi Pricing](https://serpapi.com/pricing) |

### 4. Redução de Encaminhamentos Prematuros à Revisão Manual
A decisão de encaminhamento foi desacoplada de avisos meramente informativos. O sistema separa as seguintes decisões bibliográficas independentes:
1. `identidade_confirmada`: Obra corroborada por autoridade externa ou CIP formal.
2. `identidade_plausivel_local`: Obra com título e autoria sólidos no PDF, permitindo organização automática segura.
3. `metadados_completos`: Presença de todos os campos essenciais por tipo documental (título, autor, ano, editora/periódico). A ausência de cidade resulta em notação ABNT `[S. l.]` e **não** encaminha para quarentena.
4. `tipo_documental_confiante`: Classificação Jev acima do limiar de incerteza (>= 0.50).
5. `assunto_confiante`: Taxonomia temática CNPq resolvida (não genérica "Outros/Geral").
6. `integridade_arquivo_ok`: Arquivo não corrompido e contagem de páginas compatível com obra integral.
7. `busca_externa_esgotada`: Todas as fontes pertinentes foram consultadas sem pendências técnicas de conexão/limite.
8. `revisao_manual_necessaria`: Apenas acionada quando houver incerteza real de identidade, tipologia duvidosa, conflito irreconciliável entre fontes ou integridade física comprometida.

### 5. ABNT NBR 6023:2018 Estrita
- Manipulação precisa de sobrenomes compostos, agnomes familiares (Filho, Neto, Júnior, Sobrinho) e instituições.
- Tratamento de ausência de localidade (`[S. l.]`), editora (`[s. n.]`) ou ambos (`[S. l.: s. n.]`).
- Omissão de ISBN na referência textual de livros, mantendo-o apenas nos metadados estruturados (frontmatter YAML).

---

## 📦 Instalação

Requer **Python 3.10 ou superior** (recomendado Python 3.12).

```bash
# Clone ou acesse o repositório
cd organizador-catalogador-pdf

# Crie e ative o ambiente virtual
python3 -m venv .venv
source .venv/bin/activate       # No Windows: .venv\Scripts\activate

# Instalação completa com suporte a TypeSafe AI
pip install -e ".[typesafe]"

# Ou instalação de desenvolvimento com suite de testes
pip install -e ".[dev]"
```

Configure sua chave de API no arquivo `.env`:
```bash
cp .env.example .env
# Edite .env e adicione sua chave: TYPESAFE_API_KEY=sua_chave_aqui
```

---

## 🚀 Uso

O comando oficial é **`organizador-pdf`** (ou `python -m organizador_pdf`), com atalho via `python main.py`.

### Exemplos de Uso

```bash
# 1. Simulação prévia (dry-run): analisa os PDFs e exibe o plano sem alterar o disco
organizador-pdf -i ~/Downloads/meus_pdfs -o ~/Biblioteca --dry-run

# 2. Execução padrão (copia arquivos para a árvore hierárquica CNPq com Jev Remoto)
organizador-pdf -i ~/Downloads/meus_pdfs -o ~/Biblioteca

# 3. Execução offline determinística local
organizador-pdf -i ~/Downloads/meus_pdfs -o ~/Biblioteca --classificador local

# 4. Organização em estrutura plana por categoria (<destino>/<Tipo>/)
organizador-pdf -i ~/Downloads/meus_pdfs -o ~/Biblioteca --plana

# 5. Mover arquivos originais em vez de copiar
organizador-pdf -i ~/Downloads/meus_pdfs -o ~/Biblioteca --mover

# 6. Processamento paralelo acelerado (4 workers)
organizador-pdf -i ~/Downloads/meus_pdfs -o ~/Biblioteca --paralelo 4

# 7. Modo interativo passo a passo (confirmação visual de metadados)
organizador-pdf -i ~/Downloads/meus_pdfs -o ~/Biblioteca --interactive
```

### Opções da Linha de Comando

| Opção | Padrão | Descrição |
| :--- | :--- | :--- |
| `--origem` / `--input` / `-i` | *obrigatório* | Diretório de origem com arquivos PDF |
| `--destino` / `--output` / `-o` | *obrigatório* | Diretório de destino da biblioteca organizada |
| `--estrutura` | `cnpq` | Modelo de diretórios: `cnpq` (hierárquico) ou `plana` (categoria) |
| `--plana` | `False` | Atalho para organizar em estrutura plana |
| `--classificador` | `remoto` | Modo do classificador: `remoto` / `jev_remoto` (padrão obrigatório), ou `local` |
| `--dry-run` | `False` | Simula o pipeline sem gravar no disco |
| `--resume` | `False` | Retoma o lote pendente a partir do checkpoint salvo |
| `--recursive` / `-r` | `True` | Varredura recursiva em subpastas |
| `--mover` | `False` | Move o arquivo PDF original em vez de copiar |
| `--subpasta-md` | `None` | Grava arquivos `.md` em subpasta espelho |
| `--paralelo` / `-j` | `1` | Número de processos concorrentes |
| `--quarantine` | `True` | Roteia itens com incerteza de identidade para `revisao_manual/` |
| `--interactive` / `--validate` | `False` | Modo interativo passo a passo com confirmação de metadados |
| `--verbose` / `-v` | `False` | Habilita logs informativos detalhados |

---

## 🧪 Suíte de Testes

A suíte completa possui **301 testes automatizados** e pode ser executada offline:

```bash
pytest
```

Para executar especificamente a bateria de testes de Biblioteconomia, Auditoria e Uniformidade Remota:
```bash
pytest tests/test_auditoria_biblioteconomia.py tests/test_audit_remediation.py tests/test_config.py -v
```

---

## 📄 Licença

Distribuído sob os termos da licença MIT. Consulte o arquivo `LICENSE` para mais detalhes.
