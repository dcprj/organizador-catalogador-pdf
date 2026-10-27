# Organizador e Catalogador Inteligente de PDFs — Especificação e Fonte Única da Verdade

> **Documento Mestre Definitivo do Projeto**  
> **Versão:** 0.4.3 (Robustez contra Ruído de Diagramação, Normalização Unicode e Autoridade Canônica Bibliográfica)  
> **Status:** Produção / 100% Testado (313 testes automatizados aprovados)  
> **Finalidade:** Servir como a Fonte Única da Verdade para documentação, manutenção e recriação integral do sistema.

---

## 1. Visão Geral e Missão do Projeto

O **Organizador e Catalogador Inteligente de PDFs** (`organizador-pdf`, com ponto de entrada via CLI oficial e atalho via `main.py`) é uma ferramenta de linha de comando multiplataforma de alta precisão desenhada para automatizar a ingestão, catalogação bibliográfica, enquadramento temático e arquivamento padronizado de lotes de documentos acadêmicos e editoriais em formato PDF.

Para cada documento processado, o pipeline:
1. Realiza **amostragem leve de texto nativo** (10 primeiras e 10 últimas páginas) via PyMuPDF puro, sem execução de OCR pesado e sem conversão redundante do miolo/corpo do PDF;
2. Extrai metadados bibliográficos estruturados e classifica a obra tematicamente de acordo com a **Tabela de Áreas do Conhecimento do CNPq** (Grandes Áreas e Subáreas) e sua tipologia documental (Livro, Artigo, Dissertação/Tese, etc.);
3. Valida identificadores matemáticos (ISBN-10, ISBN-13, ISSN-8, DOI) e enriquece os metadados através de consultas progressivas a APIs bibliográficas públicas e gratuitas com validação prévia de similaridade semântica;
4. Formata a referência bibliográfica oficial em estrita conformidade com a norma **ABNT NBR 6023:2018**;
5. Gera um arquivo Markdown companheiro (*companion* `.md`) contendo exclusivamente o Frontmatter YAML canônico 2.0 e a referência ABNT formatada (**sem o corpo do PDF e sem qualquer amarração ou dependência do Obsidian**);
6. Renomeia o PDF no padrão acadêmico canônico `SOBRENOME, Nome - Título (Ano).pdf` e organiza os arquivos em uma árvore de diretórios hierárquica baseada no CNPq (`<DESTINO>/<Grande Área CNPq>/<Subárea CNPq>/<Plural do Tipo>/`), isolando anomalias em `revisao_manual/`.

### Princípios Fundamentais de Engenharia
1. **Determinismo e Factualidade**: Proibição absoluta de alucinações sintéticas. Metadados devem ter correspondência direta com o documento ou validação por autoridade externa (CBL, Crossref, DataCite, Google Books).
2. **Leveza e Desempenho Instantâneo**: Amostragem seletiva de páginas essenciais. Tempo médio de execução inferior a 200ms por documento em modo sequencial e inferior a 50ms por documento em paralelo (`-j 4`).
3. **Sem Conversão do Corpo do PDF para Markdown**: O arquivo `.md` é estritamente um índice de metadados de acompanhamento (*companion*). **Não há extração nem conversão integral do miolo do livro, nem inclusão de seções como `## Conteúdo`**.
4. **Independência de Plataforma de Notas**: O Markdown e os metadados são universais e neutros para indexação direta em sistemas de arquivos e repositórios acadêmicos, sem qualquer vinculação ao Obsidian.
5. **Padronização Estrita pelas Regras do CNPq**: Árvore de pastas baseada na Tabela das Áreas do Conhecimento do CNPq e nomenclatura de arquivos no padrão acadêmico unívoco (`SOBRENOME, Nome - Título (Ano).pdf`).
6. **Privacidade e Tráfego Mínimo**: O arquivo PDF integral **NUNCA** trafega pela rede. No modo remoto, apenas até 3.000 caracteres de texto nativo de abertura são enviados. O escopo é restrito a acervos acadêmicos e editoriais; documentos clínicos ou confidenciais não são manipulados.
7. **Resiliência e Idempotência**: Falha em um arquivo individual jamais interrompe o processamento do lote. O estado é salvo atomicamente em `~/.organizador-pdf/estado.json`, permitindo retomada exata com `--resume`.
8. **Rastreabilidade e Auditoria Contínua**: Cada documento gera um registro `<nome>.auditoria.json` documentando fontes consultadas, tempos, status por provedor, valores originais vs. selecionados, e 8 decisões bibliográficas fundamentadas.

---

## 2. Histórico do Processo e Todos os Ajustes Realizados

A aplicação evoluiu através de quatro grandes fases arquiteturais:

### Fase 1: Concepção Inicial e Limitações dos LLMs Pesados (v0.1.0 - v0.2.0)
* **Abordagem Inicial**: Conversão do corpo inteiro do PDF para Markdown com `pymupdf4llm` e envio de textos longos para LLMs locais (Ollama) ou provedores pagos (Anthropic, OpenAI, DeepSeek).
* **Problemas Identificados**:
  * Latência altíssima (minutos por arquivo) e consumo excessivo de memória e tokens.
  * Alucinação frequente de identificadores (ISBNs, ISSNs e DOIs inexistentes gerados sinteticamente).
  * Falhas no executável PyInstaller por ausência dos modelos ONNX do `pymupdf4llm`.
  * Erros 400 em APIs como DeepSeek por incompatibilidade com schemas JSON estritos.
* **Primeiros Ajustes**:
  * Implementação de filtros determinísticos contra alucinação (descarte de identificadores não encontrados no texto).
  * Adição de provedores de fallback, suporte a variáveis de ambiente `.env` e persistência de lotes com `--resume`.

### Fase 2: Transição Determinística e Abandono da Conversão Integral (v0.3.0 - v0.4.0)
* **Mudança Arquitetural Radical**:
  * **Eliminação da conversão integral do corpo do PDF**: adoção definitiva da **amostragem leve de texto nativo** (10 primeiras e 10 últimas páginas) via PyMuPDF puro.
  * **Criação do Markdown Companheiro (`.md`)**: o arquivo Markdown passou a ser um companion de catalogação (metadados + referência ABNT), sem o corpo do documento.
  * Substituição dos LLMs por motor determinístico local inteligente (`classifier_jev.py`), baseado em parsing de **Ficha Catalográfica (CIP nos padrões AACR2 e ISBD)** e heurísticas bibliográficas especializadas.
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
* **Saneamento e Higienização Avançada de Títulos**:
  * Rejeição completa de identificadores (ISSN, ISBN, DOI) como títulos.
  * Descarte de disclaimers de direitos autorais e repositórios acadêmicos ("Este exemplar foi revisado...", "Todos os direitos reservados à...", etc.).
  * Truncamento e limpeza de blocos de créditos editoriais (ex.: "Coordenação editorial por...", conselho científico).
  * Limpeza de números de página residuais e códigos de classificação CDD/CDU/Cutter aderidos ao título.
  * Quarentena obrigatória para unipalavras ou verbos isolados (ex.: títulos genéricos sem contexto).
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

* **RNF1 — Linguagem e Tipagem:** Python 3.10+ (recomendado 3.12). Tipagem estática completa (`typing`), modelos de dados estruturados com `pydantic` v2 e estruturas internas em `dataclasses`.
* **RNF2 — Multiplataforma e Manipulação de Caminhos:** Compatibilidade integral com macOS, Linux e Windows. Uso exclusivo de `pathlib.Path`. Monitoramento dinâmico do comprimento absoluto dos caminhos no Windows para garantir conformidade estrita com o limite de 260 caracteres (`MAX_PATH`), truncando o título de forma elegante quando necessário.
* **RNF3 — Desempenho e Eficiência de Recursos:** Amostragem seletiva de no máximo 10 páginas iniciais e 10 páginas finais por documento. Tempo médio de processamento inferior a 200ms por arquivo em modo sequencial e inferior a 50ms por arquivo em processamento paralelo multi-threaded (`--paralelo 4`).
* **RNF4 — Resiliência, Concorrência e Idempotência:** Falhas em um arquivo nunca interrompem o lote. Tratamento seguro de concorrência com sessões thread-local (`requests.Session`) e serialização na escrita de estado e disco. Arquivo de estado salvo atomicamente em `~/.organizador-pdf/estado.json` e limpo ao final do lote.
* **RNF5 — Privacidade e Segurança de Rede:** Nenhum PDF integral trafega pela rede. Modo remoto envia no máximo 3.000 caracteres de texto nativo de abertura. Não há persistência de chaves de API no arquivo de estado.
* **RNF6 — Interface de Linha de Comando e UX:** Interface rica e moderna com `typer` e `rich`. Exibição de tabelas com identificadores, status por provedor, confiança, árvores de diretórios finais, barra de progresso em tempo real e distinção visual clara entre chamadas ao classificador remoto e consultas a APIs públicas.
* **RNF7 — Cobertura de Testes Automatizados:** Suíte com 100% dos testes passando sem necessidade de rede externa (301 testes unitários, de integração, regressão e auditoria).

---

## 4. Requisitos Funcionais (RF)

### RF1 — Interface de Linha de Comando (CLI) e Parâmetros
Ponto de entrada único via Typer com comandos e flags:
* `--origem` / `--input` / `-i`: Diretório com arquivos PDF a processar (obrigatório, salvo para `--resume`).
* `--destino` / `--output` / `-o`: Diretório de saída onde a biblioteca será estruturada (obrigatório, salvo para `--resume`).
* `--estrutura`: Modelo da árvore de diretórios: `cnpq` (hierárquico por Grande Área/Subárea/Tipo, padrão) ou `plana` (direto por Tipo).
* `--plana`: Atalho booleano para `--estrutura plana`.
* `--classificador`: Modo do classificador temático/tipológico: `remoto` ou `jev_remoto` (padrão oficial com TypeSafe AI), ou `local` (motor heurístico offline).
* `--dry-run`: Simulação completa que exibe tabelas e planejamento sem alterar o sistema de arquivos.
* `--resume`: Retomada automática de lote pendente lendo `~/.organizador-pdf/estado.json`.
* `--recursive` / `-r`: Busca recursiva em subpastas da origem (padrão: `True`).
* `--mover`: Move os arquivos PDF originais em vez de copiar.
* `--subpasta-md`: Grava os arquivos `.md` companheiros em uma pasta espelho separada.
* `--paralelo` / `-j`: Número de threads de trabalho concorrentes (padrão: `1`).
* `--quarantine`: Roteia anomalias para `<destino>/revisao_manual/<Tipo>/` (padrão: `True`).
* `--interactive` / `--validate`: Modo interativo de inspeção com confirmação de metadados em terminal.
* `--limite` / `-n`: Limite máximo de documentos a processar na sessão.
* `--log`: Caminho customizado para arquivo de log de erros (padrão: `erros.log`).
* `--verbose` / `-v`: Logs detalhados em nível DEBUG.

### RF2 — Amostragem e Extração de Texto Nativo
* Módulo `converter.py` extrai até 10 primeiras páginas e 10 últimas páginas do PDF via PyMuPDF puro.
* **Sem conversão do miolo**: o texto do corpo da obra não é extraído nem convertido para Markdown.
* Detecta páginas com menos de 30 caracteres válidos como indicativo de página gráfica/escaneada, classificando o documento como `Situacao.PENDENTE_OCR` (`requer_ocr=True`).

### RF3 — Classificação Documental e Taxonomia CNPq
* Classificação da obra baseada na **Tabela de Áreas do Conhecimento do CNPq**:
  * **Grandes Áreas**: Ciências Exatas e da Terra, Ciências Biológicas, Engenharias, Ciências da Saúde, Ciências Agrárias, Ciências Sociais Aplicadas, Ciências Humanas, Linguística Letras e Artes.
  * **Subáreas Temáticas**: Especialidade correspondente (ex.: Psicologia Social, Epistemologia, Engenharia de Software, etc.).
* **Modo Padrão (`jev_remoto` / `remoto`)**:
  * Utiliza a API da TypeSafe AI / Jev enviando apenas até 3.000 caracteres de texto nativo dos metadados de abertura.
  * Validação antecipada (fail-fast): levanta `ErroDeConfiguracao` se sem chave e `ErroDeClassificacaoRemota` se sem SDK instalado.
* **Modo Local (`local`)**:
  * Executa `classifier_jev.py` com parsing de Ficha Catalográfica (CIP nos padrões AACR2 e ISBD) e heurísticas bibliográficas especializadas.
* **Tipologias Atendidas**:
  * `Livro`, `Artigo`, `Dissertação/Tese`, `Tese`, `Apostila`, `Revista`, `Capítulo de Livro`, `Relatório`, `Trabalho em Evento`, `Instrumento/Escala`, `Documento Administrativo`, `Arquivo Parcial`, `Outros`.

### RF4 — Validação Matemática de Identificadores
* Extração e validação rigorosa de dígitos verificadores:
  * **ISBN-10**: Validação de formato e checksum módulo 11. Conversão canônica para ISBN-13 (prefixo 978).
  * **ISBN-13**: Validação de prefixo (978 ou 979) e checksum módulo 10.
  * **ISSN-8**: Validação de formato e checksum módulo 11, suportando dígito de controle `X`.
  * **DOI**: Normalização eliminando prefixos `https://doi.org/`, `http://dx.doi.org/`, `doi:`.
* Estabelecimento de 3 níveis de identificadores:
  1. *Extraído*: padrão de texto localizado no PDF.
  2. *Validado*: dígito verificador matemático confirmado.
  3. *Confirmado para a Obra*: verificado junto a registro oficial externo ou CIP formal.

### RF5 — Busca Bibliográfica Progressiva e Multi-API
Módulo `metadata_api.py` com limites e políticas oficiais:

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
  * **Verificação de Similaridade**: Campos ausentes (ano, editora, local) só são mesclados se houver correspondência semântica de título e autor entre o documento e a resposta da API.
  * **Rastreabilidade de Cache**: Consultas recuperadas de cache em disco (`~/.cache/organizador_pdf/bibliographic_cache.json`) registram `cache_hit: true` e `origem_resposta: "cache_disco"`. Consultas ao vivo recebem `origem_resposta: "rede_remota"`. Desativação configurável via `ORGPDF_CACHE_BIBLIOGRAFICO=false`.
  * **Critério Rigoroso de Busca Esgotada**: `busca_externa_esgotada` é `true` **apenas** quando buscas foram efetuadas e não restaram erros técnicos. Se zero fontes foram acionadas, registra `status_busca_externa: "incompleta_sem_consultas"` e `busca_externa_esgotada: false`.

### RF6 — Higienização e Saneamento de Metadados
* **Higienização de Títulos**:
  * Rejeição de identificadores (ISBN, ISSN, DOI) como títulos.
  * Remoção de disclaimers de direitos autorais e repositórios institucionais ("Este exemplar foi revisado pela biblioteca...", "Todos os direitos reservados à...", etc.).
  * Truncamento e limpeza de blocos de créditos editoriais (ex.: "Coordenação geral de...", conselho científico).
  * Limpeza de numerações de páginas e códigos CDD/CDU/Cutter aderidos ao título.
  * Quarentena obrigatória para unipalavras ou verbos isolados.
* **Saneamento de Datas e Locais**:
  * Distinção entre ano da edição catalográfica (ex.: 2006) e ano de reimpressão (ex.: 2017), preservando o ano original da obra.
  * Rejeição de nomes de autores ou assuntos tomados indevidamente como local de publicação (ex.: "Jung" como cidade).

### RF7 — Formatação ABNT NBR 6023:2018 Estrita
Módulo `abnt_formatter.py` formatando referências oficiais de linha única:
* Sobrenomes de autores em caixa-alta: `SOBRENOME, Nome`.
* Agnomes familiares preservados junto ao sobrenome: `SILVA FILHO, João`, `SANTOS JÚNIOR, Pedro`, `ALMEIDA NETO, Carlos`.
* Sobrenomes compostos em português e espanhol respeitados: `ESPIRITO SANTO, Maria`, `CASTELO BRANCO, Camilo`, `GARCÍA MÁRQUEZ, Gabriel`.
* Mais de três autores: primeiro autor seguido de `et al.` (ou listagem completa configurável).
* Ausência de localidade tratada como `[S. l.]`. Ausência de editora tratada como `[s. n.]`. Ambos ausentes como `[S. l.: s. n.]` (notação bibliográfica correta que **NÃO** encaminha para quarentena).
* **Omissão de ISBN na referência de livros**: o ISBN é preservado nos metadados estruturados (frontmatter YAML), mas suprimido do corpo textual da referência ABNT de livros.

### RF8 — Companion Markdown Neutro (Frontmatter 2.0)
Geração de `.md` companheiro com YAML Frontmatter padronizado e referência ABNT em bloco de citação. **O arquivo contém estritamente metadados e referência, sem texto do miolo do livro e sem amarras ou dependências com o Obsidian**:

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

### RF9 — Nomenclatura e Árvore de Diretórios pelas Regras do CNPq
* **Padrão de Nome de Arquivo**:
  * Com autor e ano: `SOBRENOME, Nome - Título (Ano).pdf` (e `.md`).
  * Múltiplos autores: `SOBRENOME, Nome et al. - Título (Ano).pdf`.
  * Com subtítulo curto: `SOBRENOME, Nome - Título - Subtítulo (Ano).pdf`.
  * Sem autor: `Título (Ano).pdf`.
  * Sem ano: `SOBRENOME, Nome - Título.pdf`.
* **Sanitização de Caracteres**: Substituição de caracteres ilegais (`\ / : * ? " < > |`) por hífen.
* **Orçamento Dinâmico MAX_PATH (Windows)**: Monitoramento do caminho absoluto para garantir conformidade com o teto de 260 caracteres do Windows, truncando o título preservando palavras inteiras.
* **Resolução de Conflitos**: Sufixos numéricos sequenciais ` (2)`, ` (3)` sem sobrescrita.
* **Estrutura de Pastas de Destino**:
  * **Organização Regular Hierárquica CNPq (`--estrutura cnpq`, padrão oficial)**:
    `<DESTINO>/<Grande_Área_CNPq>/<Subárea_CNPq>/<Plural_do_Tipo>/`
    *(Ex.: `/Biblioteca/Ciências Humanas/Psicologia/Livros/` ou `/Biblioteca/Filosofia/Epistemologia/Livros/`)*
  * **Organização Regular Plana (`--estrutura plana`)**:
    `<DESTINO>/<Plural_do_Tipo>/`
  * **Quarentena de Revisão Manual**:
    `<DESTINO>/revisao_manual/<Tipo>/`

### RF10 — Decisões Bibliográficas e Auditoria Contínua
Desacoplamento de avisos informativos das decisões reais de quarentena. O sistema avalia 8 decisões bibliográficas independentes:
1. `identidade_confirmada`: Obra corroborada por autoridade externa ou CIP formal.
2. `identidade_plausivel_local`: Título consistente e autoria válida extraídos do PDF (permite organização segura).
3. `metadados_completos`: Presença de título, autor, ano e editora/periódico. Falta de cidade vira `[S. l.]` e **não** gera quarentena.
4. `tipo_documental_confiante`: Classificação Jev acima de 0.50 e não "Outros".
5. `assunto_confiante`: Taxonomia CNPq resolvida (não "Outros/Geral").
6. `integridade_arquivo_ok`: Arquivo não corrompido e contagem de páginas íntegra.
7. `busca_externa_esgotada`: Todas as fontes pertinentes foram consultadas sem pendências técnicas de conexão/limite.
8. `revisao_manual_necessaria`: Ativada **exclusivamente** por incerteza de identidade, tipologia duvidosa, conflito irreconciliável entre fontes ou integridade violada.

* **Arquivo `<nome>.auditoria.json`**: Gravado para cada arquivo organizado contendo hash SHA-256, tempos, resposta Jev, fontes consultadas com `cache_hit`, valores originais vs. finais e o quadro das decisões bibliográficas fundamentadas.
* **Relatório Formal em PDF**: Gerado via `scripts/gerar_relatorio_pdf.py` para auditoria executiva e documental.

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
│       ├── converter.py                   # Amostragem 10+10 páginas, frontmatter 2.0 e Markdown companion
│       ├── estado.py                      # Gerenciamento de checkpoint atômico para --resume
│       ├── extractor.py                   # Exceções de extração e compatibilidade de interface
│       ├── logging_utils.py               # Sistema de logs com registro em arquivo e console
│       ├── metadata_api.py                # Clientes Multi-API (Brasil API, Crossref, etc.) com cache
│       ├── models.py                      # Modelos de dados e validações Pydantic v2
│       ├── organizer.py                   # Operações de disco, MAX_PATH e hierarquia CNPq
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

## 6. Prompt Mestre Definitivo de Recriação do Sistema

> Copie o bloco abaixo para recriar o projeto do zero com um assistente de código ou desenvolvedor caso o repositório seja perdido.

```
Você é um desenvolvedor especialista em Python. Construa uma ferramenta CLI multiplataforma (macOS/Linux/Windows) chamada "organizador-pdf" que processa lotes de PDFs acadêmicos e editoriais seguindo os padrões CNPq e ABNT:

1. Amostragem Leve de Texto: Extraia até 10 primeiras e 10 últimas páginas usando PyMuPDF (sem OCR automático e sem converter o miolo do livro). Detecte páginas escaneadas (< 30 caracteres válidos) marcando PENDENTE_OCR.
2. Classificação Documental e Taxonomia CNPq:
   - Modo padrão 'jev_remoto': utiliza a API da TypeSafe AI / Jev enviando apenas amostra inicial de até 3.000 caracteres, com fail-fast caso a chave TYPESAFE_API_KEY ou typesafe-sdk estejam ausentes (sem fallback silencioso).
   - Modo 'local': motor heurístico determinístico com parser de Ficha Catalográfica (CIP nos padrões AACR2 e ISBD) e colofão.
   - Categorize o tipo documental (Livro, Artigo, Dissertação/Tese, Apostila, Relatório, etc.) e mapeie para a Tabela de Áreas do Conhecimento do CNPq (Grande Área e Subárea).
3. Identificadores Matemáticos: Valide checksum de ISBN-10 (módulo 11 com conversão para ISBN-13), ISBN-13 (módulo 10), ISSN-8 (módulo 11 com 'X') e normalize DOIs.
4. Busca e Enriquecimento Multi-API: Consulte progressivamente Brasil API / CBL, Crossref, DataCite, Google Books, OpenLibrary e OpenAlex, validando similaridade de título/autor antes de mesclar campos faltantes. Registre cache_hit e origem_resposta ("cache_disco" vs "rede_remota").
5. Higienização Estrita de Títulos: Rejeite identificadores como títulos, descarte disclaimers universitários, créditos editoriais, números de página residuais e códigos CDD/CDU/Cutter. Distinga ano de edição de ano de reimpressão.
6. Formatação ABNT NBR 6023:2018: Autores em caixa-alta, agnomes familiares (Filho, Júnior, Neto), sobrenomes compostos, notação [S. l.] e [s. n.] (sem mandar para quarentena) e omissão de ISBN na referência textual de livros.
7. Arquivo Companion Markdown (.md): Gere um arquivo Markdown estritamente com YAML Frontmatter 2.0 e Referência Bibliográfica ABNT formatada. NÃO inclua o texto original nem seções de conteúdo do PDF. Não adicione dependências de ferramentas externas (como Obsidian).
8. Nomenclatura e Organização CNPq:
   - Nome do arquivo no padrão acadêmico: "SOBRENOME, Nome - Título (Ano).pdf" (e ".md"), com salvaguarda MAX_PATH do Windows (260 caracteres) e desduplicação (2), (3).
   - Diretórios de destino organizados na hierarquia CNPq: "<DESTINO>/<Grande Área>/<Subárea>/<Plural do Tipo>/" (ou estrutura plana com --plana).
   - Quarentena em "<DESTINO>/revisao_manual/<Tipo>/" acionada estritamente por incerteza real de identidade, tipologia duvidosa ou corrupção do arquivo.
9. Rastreabilidade e Auditoria: Gere "<nome>.auditoria.json" por arquivo registrando fontes, tempos, scores e as 8 decisões bibliográficas independentes.
10. CLI e Operação: Implemente com Typer e Rich suportando --origem, --destino, --estrutura (cnpq|plana), --classificador (remoto|local), --dry-run, --resume (estado atômico em ~/.organizador-pdf/estado.json), --paralelo (-j), --mover, --subpasta-md, --quarantine, --interactive e --verbose.
11. Testes: Suíte completa com 301 testes pytest executáveis 100% offline via mocks.
```
