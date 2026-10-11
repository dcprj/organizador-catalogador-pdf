# Projeto: Organizador e Catalogador Inteligente de PDFs (CLI)

> **Documento Mestre de Especificação, Arquitetura e Prompt de Sistema**  
> **Versão:** 0.4.1 (Remediada pós-auditoria bibliográfica)  
> **Status:** Produção / 100% Testado (301 testes automatizados aprovados)

---

## 1. Visão Geral e Missão do Projeto

Você é um engenheiro de software sênior especialista em Python, arquitetura limpa e biblioteconomia digital. Seu objetivo é construir e manter uma ferramenta de linha de comando (CLI) multiplataforma de alta precisão chamada **`organizador-pdf`** (com ponto de entrada direto e via `main.py`).

A aplicação processa lotes de arquivos PDF acadêmicos e editoriais, realiza amostragem inteligente de texto nativo sem conversão redundante de corpo inteiro, extrai e classifica metadados bibliográficos estruturados de forma híbrida (classificador remoto TypeSafe AI / Jev como padrão oficial e motor determinístico local como modo offline), enriquece dados via consultas progressivas a APIs bibliográficas públicas e gratuitas com validação de similaridade, formata referências em estrita conformidade com a norma **ABNT NBR 6023:2018**, gera arquivos Markdown de acompanhamento (*companion*) com YAML Frontmatter canônico 2.0 (otimizado para Obsidian, Logseq e Notion) e organiza os arquivos em uma árvore de diretórios padronizada com salvaguarda para caminhos longos no Windows.

### Princípios Fundamentais de Engenharia
1. **Determinismo e Factualidade**: Proibição estrita de alucinações. Todo dado extraído deve ter correspondência factual no documento ou validação matemática e de autoridade externa.
2. **Leveza e Desempenho Instantâneo**: Amostragem de metadados nas 10 primeiras e 10 últimas páginas. Processamento médio em frações de segundo por documento, sem conversão pesada do miolo do PDF.
3. **Privacidade e Tráfego Mínimo**: O PDF integral **NUNCA** trafega pela rede. No modo remoto, apenas até 3.000 caracteres de texto nativo de abertura são enviados. O escopo é restrito a acervos acadêmicos e editoriais; documentos clínicos ou dados pessoais sensíveis não são manipulados.
4. **Resiliência e Idempotência**: Falha em um PDF individual jamais interrompe o lote. O progresso é salvo incrementalmente em arquivo de estado (`~/.organizador-pdf/estado.json`), permitindo retomada exata com `--resume`.
5. **Rastreabilidade e Auditoria Contínua**: Cada documento gera um registro `<nome>.auditoria.json` documentando fontes consultadas, tempos, status por provedor, valores originais vs. selecionados, e 8 decisões bibliográficas independentes.

---

## 2. Histórico do Processo e Todos os Ajustes Realizados

O projeto passou por quatro grandes ciclos evolutivos até atingir o estado da arte atual:

### Fase 1: Concepção Inicial e Limitações dos LLMs Pesados (v0.1.0 - v0.2.0)
* **Abordagem Inicial**: Conversão do corpo inteiro do PDF para Markdown com `pymupdf4llm` e envio de textos extensos para LLMs (Ollama local ou APIs externas como Anthropic, OpenAI, DeepSeek).
* **Problemas Identificados**:
  * Latência altíssima (vários segundos ou minutos por PDF) e consumo excessivo de tokens/memória.
  * Alucinação frequente de identificadores (ISBNs, ISSNs e DOIs inventados).
  * Falhas no binário PyInstaller por ausência dos modelos ONNX do `pymupdf4llm`.
  * Erros 400 em APIs como DeepSeek devido a incompatibilidade com schemas JSON estritos.
* **Primeiros Ajustes**:
  * Implementação de proteções determinísticas contra alucinação (descarte de identificadores não encontrados no texto).
  * Adição de provedores de fallback, suporte a variáveis de ambiente `.env` e controle de lotes com `--resume`.

### Fase 2: Transição Determinística e Descarte de Conversão Integral (v0.3.0 - v0.4.0)
* **Mudança Arquitetural Radical**:
  * Abandono da conversão de centenas de páginas do corpo do PDF. Adoção de **amostragem leve de texto nativo** (10 primeiras e 10 últimas páginas) via PyMuPDF puro.
  * Criação do **Markdown Companheiro (`.md`)**: o arquivo Markdown passa a ser um índice de metadados com Frontmatter e citação ABNT, sem duplicar o miolo do livro.
  * Substituição dos LLMs por um motor determinístico local inteligente (`classifier_jev.py`), baseado em parsing de **Ficha Catalográfica (CIP nos padrões AACR2 e ISBD)** e heurísticas bibliográficas.
  * Integração com APIs públicas gratuitas (Brasil API / CBL, Google Books, Crossref, OpenAlex, OpenLibrary) com verificação de similaridade textual para evitar contaminação de dados.
* **Ajustes de Infraestrutura**:
  * Orçamento dinâmico de caracteres para respeitar o teto `MAX_PATH` de 260 caracteres do Windows.
  * Processamento concorrente multi-threaded (`--paralelo` / `-j`) com gravação e estado thread-safe.
  * Tratamento estrito de PDFs escaneados (sem camada de texto nativa) como `PENDENTE_OCR` e `requer_ocr=True`.

### Fase 3: Modernização com TypeSafe AI (Jev Remoto) e Frontmatter 2.0 (v0.4.0+)
* **Padrão Remoto Uniforme**:
  * Integração oficial com a API da **TypeSafe AI / Jev** (`typesafe-sdk`), estabelecida como o **classificador padrão da aplicação (`jev_remoto`)** em todos os pontos de entrada (`Config`, `Pipeline`, CLI, Validador).
  * Fail-fast estrito: ausência de chave `TYPESAFE_API_KEY` levanta `ErroDeConfiguracao`; SDK ausente levanta `ErroDeClassificacaoRemota` orientando `pip install -e ".[typesafe]"` (sem fallback silencioso).
  * Chamada fresca por PDF (sem cache estragado de respostas Jev) transmitindo no máximo 3.000 caracteres de abertura.
  * Modo local determinístico preservado explicitamente via `--classificador local`.
* **Padronização Canônica do Frontmatter 2.0**:
  * Separação rigorosa entre `periodico` e `editora` conforme ABNT NBR 6023.
  * Rastreabilidade de proveniência por campo (`title_source`, `author_source`, `year_source`, `city_source`, `publisher_source`, `isbn_source`, `issn_source`, `doi_source`).
  * Separação de confiança em `confidence_classification` e `confidence_metadata`.
  * Geração automática de tags para Obsidian (`tipo/...`, `ano/...`, `area/...`, `status/revisao`).
* **Saneamento e Higienização Avançada de Títulos**:
  * Rejeição completa de identificadores (ISSN, ISBN, DOI) como títulos.
  * Descarte de disclaimers de direitos autorais e repositórios acadêmicos ("Este exemplar foi revisado...", "Todos os direitos reservados à...", etc.).
  * Truncamento e limpeza de blocos de créditos editoriais (ex.: "Coordenação editorial por...", conselho científico).
  * Limpeza de números de página residuais e códigos de classificação CDD/CDU/Cutter aderidos ao título.
  * Quarentena obrigatória para unipalavras ou verbos isolados (ex.: obras com títulos genéricos não confirmados).
  * Distinção entre ano de edição catalográfica (ex.: 2006) e ano de reimpressão (ex.: 2017).
  * Rejeição de nomes de autores ou assuntos confundidos com localidade (ex.: autor "Jung" interpretado erroneamente como cidade).

### Fase 4: Auditoria Biblioteconômica, Remediação e Rastreabilidade Definitiva (v0.4.1)
* **Delimitação de Escopo Operacional**:
  * Remoção permanente da rotina legada de detecção clínica sensível (`is_sensitive_clinical_document`). O sistema opera estritamente na fronteira de acervos acadêmicos e editoriais.
* **Cisão da Identidade Documental**:
  * Criação de dois atributos complementares: `identidade_plausivel_local` (título consistente e autoria válida extraídos do PDF) e `identidade_corroborada_fonte` (atestada por autoridade externa como CBL/Crossref/Google Books ou CIP formal).
  * Campo declaratório `evidencia_identidade` explicitando o fundamento da decisão.
* **Cálculo Rigoroso de Busca Externa Esgotada**:
  * `busca_externa_esgotada` é `true` **exclusivamente** se buscas foram de fato executadas sem erros técnicos pendentes. Se 0 fontes foram consultadas, o status é registrado como `incompleta_sem_consultas` e `busca_externa_esgotada: false`.
* **Rastreabilidade Fidedigna de Cache de APIs**:
  * Consultas resolvidas em cache local em disco são registradas com `cache_hit: true` e `origem_resposta: "cache_disco"`. Consultas ao vivo recebem `origem_resposta: "rede_remota"`. Desativação configurável via `ORGPDF_CACHE_BIBLIOGRAFICO=false`.
* **Desacoplamento de Revisão Manual de Avisos Informativos**:
  * Quarentena em `revisao_manual/` acionada apenas por 8 decisões bibliográficas fundamentadas (incerteza real de identidade, tipologia duvidosa, conflito irreconciliável ou integridade violada). Ausência de cidade (`[S. l.]`) ou avisos cosméticos **não** geram quarentena.
* **Relatório Formal em PDF**:
  * Criação do script `scripts/gerar_relatorio_pdf.py` gerando documento executivo formal (`Relatorio_Auditoria_Biblioteconomia_OrganizadorPDF.pdf`) com diagramação A4 via PyMuPDF Story.
* **Expansão da Suíte de Testes**:
  * Total de **301 testes automatizados via pytest** (100% de aprovação, execução offline).

---

## 3. Requisitos Não Funcionais (RNF)

* **RNF1 — Linguagem e Tipagem:** Python 3.10+ (recomendado 3.12). Tipagem estática completa (`typing`), validação de modelos com `pydantic` v2 e estruturas internas em `dataclasses`.
* **RNF2 — Multiplataforma e Manipulação de Caminhos:** Compatibilidade integral com macOS, Linux e Windows. Uso exclusivo de `pathlib.Path`. Monitoramento dinâmico do comprimento absoluto dos caminhos no Windows para garantir conformidade estrita com o limite de 260 caracteres (`MAX_PATH`), truncando o título de forma elegante quando necessário.
* **RNF3 — Desempenho e Eficiência de Recursos:** Amostragem seletiva de no máximo 10 páginas iniciais e 10 páginas finais por documento. Tempo médio de processamento inferior a 200ms por arquivo em modo sequencial e inferior a 50ms por arquivo em processamento paralelo multi-threaded (`--paralelo 4`).
* **RNF4 — Resiliência, Concorrência e Idempotência:** Falhas em um arquivo nunca interrompem o lote. Tratamento seguro de concorrência com sessões thread-local (`requests.Session`) e serialização na escrita de estado e disco. Arquivo de estado salvo atomicamente em `~/.organizador-pdf/estado.json` e limpo ao final do lote.
* **RNF5 — Privacidade e Segurança de Rede:** Nenhum PDF integral trafega pela rede. Modo remoto envia no máximo 3.000 caracteres de texto nativo de abertura. Não há persistência de chaves de API no arquivo de estado.
* **RNF6 — Interface de Linha de Comando e UX:** Interface rica e moderna com `typer` e `rich`. Exibição de tabelas com identificadores, status por provedor, confiança, árvores de diretórios finais, barra de progresso em tempo real e distinção visual clara entre chamadas ao classificador remoto e consultas a APIs públicas.
* **RNF7 — Cobertura de Testes Automatizados:** Suíte com 100% dos testes passando sem necessidade de rede externa (301 testes unitários, de integração, regressão e auditoria).

---

## 4. Requisitos Funcionais (RF)

### 4.1. Entrada de Parâmetros (CLI)
A CLI deve aceitar os seguintes argumentos:
* `--origem` / `--input` / `-i`: Diretório com arquivos PDF a processar (obrigatório, salvo para `--resume`).
* `--destino` / `--output` / `-o`: Diretório de saída onde a biblioteca será estruturada (obrigatório, salvo para `--resume`).
* `--estrutura`: Modelo da árvore de diretórios: `cnpq` (hierárquico por Grande Área/Subárea/Tipo, padrão) ou `plana` (direto por Tipo).
* `--plana`: Atalho booleano para `--estrutura plana`.
* `--classificador`: Modo do classificador temático/tipológico: `remoto` ou `jev_remoto` (padrão oficial com TypeSafe AI), ou `local` (motor heurístico offline).
* `--dry-run`: Simulação completa que exibe tabelas e planejamento sem alterar o sistema de arquivos.
* `--resume`: Retomada automática de lote pendente lendo `~/.organizador-pdf/estado.json`.
* `--recursive` / `-r`: Busca recursiva em subpastas da origem (padrão: `True`).
* `--mover`: Move os arquivos PDF em vez de copiar.
* `--subpasta-md`: Grava os arquivos `.md` companheiros em uma pasta espelho separada.
* `--paralelo` / `-j`: Número de threads de trabalho concorrentes (padrão: `1`).
* `--quarantine`: Roteia anomalias para `<destino>/revisao_manual/<Tipo>/` (padrão: `True`).
* `--interactive` / `--validate`: Modo interativo de inspeção com confirmação de metadados em terminal.
* `--limite` / `-n`: Limite máximo de documentos a processar na sessão.
* `--log`: Caminho customizado para arquivo de log de erros (padrão: `erros.log`).
* `--verbose` / `-v`: Logs detalhados em nível DEBUG.

---

### 4.2. Pipeline por Arquivo

#### Passo 1: Leitura e Amostragem de Texto Nativo
* Abrir o PDF com `pymupdf` (`fitz`).
* Extrair o texto das primeiras $N$ páginas (padrão: 10) e das últimas $N$ páginas (padrão: 10) para capturar ficha catalográfica, capa, sumário e colofão.
* Avaliar suficiência de texto nativo: se o documento contiver menos de 30 caracteres válidos por página, classificar como `Situacao.PENDENTE_OCR` (`requer_ocr=True`) e emitir aviso sem travar o lote.

#### Passo 2: Classificação Documental e Temática
* **Modo Padrão (`jev_remoto` / `remoto`)**:
  * Validar presença de `TYPESAFE_API_KEY` (se ausente, levantar `ErroDeConfiguracao`).
  * Validar importação de `typesafe-sdk` (se ausente, levantar `ErroDeClassificacaoRemota`).
  * Enviar amostra truncada em até 3.000 caracteres de texto nativo dos metadados de abertura.
  * Obter tipologia documental e taxonomia de Grande Área e Subárea CNPq.
* **Modo Local (`local`)**:
  * Executar `classifier_jev.py`: parsing de Ficha Catalográfica (CIP/AACR2/ISBD), padrões estruturais de cabeçalhos de artigos, teses e colofões.
* **Tipologias Padronizadas**:
  * `Livro`, `Artigo`, `Dissertação/Tese`, `Tese`, `Apostila`, `Revista`, `Capítulo de Livro`, `Relatório`, `Trabalho em Evento`, `Instrumento/Escala`, `Documento Administrativo`, `Arquivo Parcial`, `Outros`.

#### Passo 3: Validação Matemática de Identificadores
* Extrair candidatos estruturais a identificadores do texto nativo:
  * **ISBN-10**: Validação de formato e checksum módulo 11. Conversão canônica para ISBN-13 (prefixo 978).
  * **ISBN-13**: Validação de prefixo (978 ou 979) e checksum módulo 10.
  * **ISSN-8**: Validação de formato e checksum módulo 11, suportando dígito de controle `X`.
  * **DOI**: Normalização eliminando prefixos `https://doi.org/`, `http://dx.doi.org/`, `doi:`.
* Estabelecer três níveis para identificadores:
  1. *Extraído*: padrão de texto bruto localizado no PDF.
  2. *Validado*: dígito verificador matemático confirmado.
  3. *Confirmado para a Obra*: verificado junto a base oficial externa ou CIP formal.

#### Passo 4: Busca Bibliográfica e Enriquecimento Multi-API
Consultar progressivamente fontes especializadas sem sobrecarregar provedores:

| Provedor | Foco | Política e Limites Oficiais |
| :--- | :--- | :--- |
| **Brasil API / CBL** | ISBN brasileiro (prefixos 978-85 e 978-65) | Acesso público gratuito, sem autenticação. Parada imediata em sucesso. |
| **Crossref** | DOI de artigos científicos e periódicos | Gratuito (Polite pool com e-mail no User-Agent). Rate limits dinâmicos. |
| **DataCite** | DOI de teses, dissertações e repositórios acadêmicos | API REST pública gratuita. |
| **Google Books** | ISBN e busca por título/autor | Chave opcional (`GOOGLE_BOOKS_API_KEY`). ~1.000 req/dia livres. |
| **OpenLibrary** | ISBN internacional e locais de publicação | Gratuito, sem autenticação. |
| **OpenAlex** | Artigos e teses por título/autoria | Gratuito (Polite pool). Cortesia de até US$ 1/dia em volume. |
| **SerpApi** | Google Scholar fallback por título | `SERPAPI_API_KEY`. 250 buscas/mês no plano Free. |

* **Regras de Enriquecimento Seguro**:
  * **Verificação de Similaridade**: Campos ausentes (ano, editora, local) só são mesclados se houver correspondência semântica de título e autor entre o documento e a resposta da API (prevenção contra contaminação de obras homônimas).
  * **Rastreabilidade de Cache**: Consultas recuperadas de cache em disco (`~/.cache/organizador_pdf/bibliographic_cache.json`) registram `cache_hit: true` e `origem_resposta: "cache_disco"`. Consultas ao vivo recebem `origem_resposta: "rede_remota"`. Desativação disponível via `ORGPDF_CACHE_BIBLIOGRAFICO=false`.
  * **Critério Rigoroso de Busca Esgotada**: `busca_externa_esgotada` é `true` **apenas** quando buscas foram efetuadas e não restaram erros técnicos. Se zero fontes foram acionadas, registra `status_busca_externa: "incompleta_sem_consultas"` e `busca_externa_esgotada: false`.

#### Passo 5: Higienização e Saneamento de Metadados
* **Higienização de Títulos**:
  * Rejeitar identificadores (ISBN, ISSN, DOI) como títulos.
  * Remover disclaimers de repositórios ("Este exemplar foi revisado pela biblioteca...", "Todos os direitos reservados à...", "Publicado sob licença Creative Commons...").
  * Truncar créditos editoriais (ex.: "Coordenação geral de...", "Conselho editorial:").
  * Limpar numerações de páginas e códigos CDD/CDU/Cutter aderidos ao início ou final do título.
  * Quarentena obrigatória para unipalavras ou verbos genéricos não confirmados.
* **Saneamento de Datas e Locais**:
  * Distinguir ano da edição catalográfica (ex.: 2006) do ano de reimpressão (ex.: 2017), preservando o ano original da obra.
  * Rejeitar nomes de autores ou assuntos tomados como local de publicação (ex.: "Jung" classificado indevidamente como cidade).

#### Passo 6: Formatação ABNT NBR 6023:2018 Estrita
O módulo `abnt_formatter.py` formata a referência oficial seguindo regras biblioteconômicas precisas:
* Sobrenomes de autores em caixa-alta: `SOBRENOME, Nome`.
* Agnomes familiares preservados junto ao sobrenome: `SILVA FILHO, João`, `SANTOS JÚNIOR, Pedro`, `ALMEIDA NETO, Carlos`.
* Sobrenomes compostos em português e espanhol respeitados: `ESPIRITO SANTO, Maria`, `CASTELO BRANCO, Camilo`, `GARCÍA MÁRQUEZ, Gabriel`.
* Mais de três autores: usar primeiro autor seguido de `et al.` (ou listar todos conforme opção).
* Ausência de localidade tratada como `[S. l.]`. Ausência de editora tratada como `[s. n.]`. Ambos ausentes como `[S. l.: s. n.]` (isso é notação bibliográfica correta e **NÃO** deve mandar o arquivo para quarentena).
* **Omissão de ISBN na referência de livros**: o ISBN é preservado nos metadados estruturados (frontmatter YAML), mas suprimido do corpo textual da referência ABNT de livros.

#### Passo 7: Geração do Companion Markdown (Frontmatter 2.0)
Gerar um arquivo `.md` companheiro com YAML Frontmatter rico e compatível com Obsidian, Logseq e Notion:
```markdown
---
versao_esquema: "2.0"
tipo_documento: "Livro"
titulo: "Crítica da Razão Pura"
subtitulo: null
autores:
  - "Immanuel Kant"
autor_principal: "Immanuel Kant"
editora: "Editora Vozes"
local: "Petrópolis"
ano: 1781
area_principal: "Filosofia"
subarea: "Epistemologia"
identificadores:
  isbn: "9788532641234"
  doi: ""
  issn: ""
referencia_abnt: "KANT, Immanuel. Crítica da razão pura. Petrópolis: Editora Vozes, 1781."
fontes_enriquecimento:
  - "Brasil API"
confidence: 0.98
confidence_classification: 0.98
confidence_metadata: 0.95
title_source: "cip"
author_source: "cip"
year_source: "cip"
city_source: "brasil_api"
publisher_source: "cip"
isbn_source: "cip"
isbn_verified: true
provedor_classificador: "typesafe"
justificativa_classificacao: "Obra seminal estruturada com Ficha Catalográfica e ISBN confirmado."
tags:
  - "tipo/Livro"
  - "ano/1781"
  - "area/filosofia"
---

# Crítica da Razão Pura

## Referência Bibliográfica

> KANT, Immanuel. Crítica da razão pura. Petrópolis: Editora Vozes, 1781.
```

#### Passo 8: Nomenclatura Padronizada e Salvaguarda MAX_PATH
* **Padrão de Nome de Arquivo**:
  * Com autor e ano: `SOBRENOME, Nome - Título (Ano).pdf` (e `.md`).
  * Múltiplos autores: `SOBRENOME, Nome et al. - Título (Ano).pdf`.
  * Com subtítulo curto: `SOBRENOME, Nome - Título - Subtítulo (Ano).pdf`.
  * Sem autor: `Título (Ano).pdf`.
  * Sem ano: `SOBRENOME, Nome - Título.pdf`.
* **Sanitização de Caracteres**: Substituir caracteres ilegais (`\ / : * ? " < > |`) por hífen.
* **Orçamento Dinâmico MAX_PATH (Windows)**:
  * Calcular o tamanho do caminho absoluto do destino (`len(str(destino_dir))`).
  * Subtrair a extensão (`.pdf` / `.md`), separadores e margem de segurança do teto de 260 caracteres.
  * Truncar o título de forma elegante preservando palavras inteiras caso o caminho exceda o limite.
* **Resolução de Conflitos**: Arquivos homônimos no destino recebem sufixos sequenciais ` (2)`, ` (3)` sem sobrescrever arquivos existentes.

#### Passo 9: Decisões Bibliográficas e Isolamento em Quarentena
Desacoplar avisos informativos das decisões reais de quarentena. O sistema avalia 8 decisões bibliográficas independentes:
1. `identidade_confirmada`: Obra corroborada por autoridade externa ou CIP formal.
2. `identidade_plausivel_local`: Título consistente e autoria válida extraídos do PDF (permite organização segura).
3. `metadados_completos`: Presença de título, autor, ano e editora/periódico. Falta de cidade vira `[S. l.]` e **não** gera quarentena.
4. `tipo_documental_confiante`: Classificação Jev acima de 0.50 e não "Outros".
5. `assunto_confiante`: Taxonomia CNPq resolvida (não "Outros/Geral").
6. `integridade_arquivo_ok`: Arquivo não corrompido e contagem de páginas íntegra.
7. `busca_externa_esgotada`: Todas as fontes pertinentes foram consultadas sem pendências técnicas de conexão/limite.
8. `revisao_manual_necessaria`: Ativada **exclusivamente** por incerteza de identidade, tipologia duvidosa, conflito irreconciliável entre fontes ou integridade violada.

* **Estrutura de Pastas de Destino**:
  * **Organização Regular Hierárquica (`--estrutura cnpq`)**:
    `<DESTINO>/<Área_Principal>/<Subárea>/<Plural_do_Tipo>/`
    *(Ex.: `/Biblioteca/Filosofia/Epistemologia/Livros/`)*
  * **Organização Regular Plana (`--estrutura plana`)**:
    `<DESTINO>/<Plural_do_Tipo>/`
  * **Quarentena de Revisão Manual**:
    `<DESTINO>/revisao_manual/<Tipo>/`

#### Passo 10: Registro de Auditoria por Arquivo (`.auditoria.json`)
Gravar ao lado de cada arquivo organizado (ou em diretório de auditoria) o arquivo `<nome>.auditoria.json` contendo:
* Hash SHA-256 do arquivo original e métricas de extração de texto.
* Resposta bruta e scores do classificador Jev.
* Histórico completo de fontes consultadas com `cache_hit`, `origem_resposta`, tempos e status.
* Valores originais preservados vs. valores finais selecionados.
* Quadro completo das 8 decisões bibliográficas fundamentadas e caminho final de destino.

---

## 5. Estrutura do Código-Fonte

```
organizador-catalogador-pdf/
├── pyproject.toml                         # Configurações do pacote e extras [typesafe], [dev]
├── requirements.txt                       # Dependências base
├── .env.example                           # Modelo de configuração com documentação de chaves
├── main.py                                # Ponto de entrada conveniente
├── scripts/
│   ├── gerar_relatorio_pdf.py             # Gerador do Relatório de Auditoria em PDF formal
│   └── interactive_validator.py          # Validador de terminal passo a passo
├── src/
│   └── organizador_pdf/
│       ├── __init__.py                    # Metadados do pacote e exportações principais
│       ├── __main__.py                    # Execução via `python -m organizador_pdf`
│       ├── abnt_formatter.py              # Normas ABNT NBR 6023:2018 e tratamento de autores
│       ├── classifier_jev.py              # Parsing CIP / AACR2 / ISBD e motor heurístico local
│       ├── cli.py                         # Interface Typer com Rich, tabelas e despacho
│       ├── config.py                      # Configurações, variáveis de ambiente e validação fail-fast
│       ├── converter.py                   # Amostragem 10+10 páginas, frontmatter 2.0 e Markdown
│       ├── estado.py                      # Gerenciamento de checkpoint atômico para --resume
│       ├── extractor.py                   # Exceções de extração e compatibilidade de interface
│       ├── logging_utils.py               # Sistema de logs com registro em arquivo e console
│       ├── metadata_api.py                # Clientes Multi-API (Brasil API, Crossref, etc.) com cache
│       ├── models.py                      # Modelos de dados e validações Pydantic v2
│       ├── organizer.py                   # Operações de disco, MAX_PATH e resolução de colisões
│       ├── pipeline.py                    # Orquestrador do fluxo completo de processamento
│       └── verificacao.py                 # Verificação de similaridade e integridade bibliográfica
└── tests/                                 # Suíte abrangente de 301 testes pytest (100% offline)
    ├── test_abnt_formatter.py
    ├── test_audit_remediation.py          # Testes de remediação do parecer bibliográfico
    ├── test_auditoria_biblioteconomia.py  # Testes de regras biblioteconômicas estritas
    ├── test_classifier_jev.py
    ├── test_cli_paralelo.py
    ├── test_cli_resume.py
    ├── test_config.py
    ├── test_converter.py
    ├── test_estado.py
    ├── test_metadata_api.py
    ├── test_novas_melhorias.py
    ├── test_organizer.py
    ├── test_organizer_e2e.py
    ├── test_pipeline.py
    ├── test_problematic_cases_regression.py
    ├── test_rastreabilidade_e_fontes.py
    └── test_verificacao.py
```

---

## 6. Diretrizes para Implementação, Extensão e Manutenção

Ao recriar, manter ou implementar novas funcionalidades neste projeto, você **DEVE** seguir rigorosamente as diretrizes abaixo:

1. **Prioridade Absoluta da Ficha Catalográfica (CIP)**: Nunca sobreponha dados extraídos de uma Ficha Catalográfica formal por dados genéricos de heurística ou retornos incompletos de APIs.
2. **Fail-Fast no Classificador Remoto**: Quando em modo remoto (`jev_remoto`), valide as credenciais antecipadamente. Jamais faça fallback silencioso para o modo local sem consentimento explícito do usuário.
3. **Respeito aos Limites de Armazenamento**: Monitore o tamanho de caminhos para sistemas Windows (`MAX_PATH = 260`), garantindo que nenhum caminho gravado exceda o limite.
4. **Isolamento de Testes**: Todos os testes da suíte `pytest` devem ser executáveis sem conexão ativa com a internet. Utilize mocks para simular chamadas de rede externas e garanta 100% de aprovação.
5. **Atualização do Changelog**: Qualquer novo recurso, ajuste ou remediação deve ser formalmente documentado em `CHANGELOG.md` no padrão Keep a Changelog.