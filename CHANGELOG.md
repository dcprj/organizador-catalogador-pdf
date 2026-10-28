# Changelog

Todas as mudanças notáveis deste projeto são documentadas aqui.

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o
projeto adota [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [0.5.1] - 2026-10-28

### Adicionado
- **Tolerância a OCR em `parse_page_cip`**:
  - Reconhece `COO`/`CDO` como CDD/CDU, autor com ponto antes da vírgula (`Conez., Célia Martins`) e a barra `/` lida como ` I ` ou `|`; limpa pontuação residual dos autores e recorre às entradas secundárias de autor da ficha quando a responsabilidade não é extraída.
- **Brasil API Multi-Provider**:
  - Quando o registro vem sem autores/ano (ou falha em 404/5xx para ISBN brasileiro), consulta `?providers=cbl|mercado-editorial|google-books|open-library` e mescla os campos ausentes; entradas antigas de cache confirmadas sem autoria são complementadas uma única vez.
- **OpenLibrary `jscmd=data` e Cascata por ISBN**:
  - Extração de autores, editora, local, ano e assuntos via `api/books?jscmd=data`; resolução de autores no fallback `/isbn/*.json`.
  - Novos `fetch_crossref_by_isbn`, `fetch_openalex_by_isbn` e `fetch_web_search_by_isbn` (determinístico, sem alucinação), acionados somente quando autoria ou ano seguem ausentes.

### Alterado
- **Blindagem do Cache em Disco**:
  - Erros transitórios (HTTP 429/5xx, timeout, falha de conexão) não são mais persistidos nem carregados do disco (purga de entradas antigas), mantendo-se apenas em memória na sessão; Google Books ganha 1 retry após 429 e cache de título em formato `(resultado, status)`.
- **Títulos Interrogativos Canônicos e Subtítulos Longos**:
  - Aceita *O que é lugar de fala?*, *Como trabalha um psicanalista?* e títulos `Título: Subtítulo` de até 27 palavras; continuam rejeitadas perguntas de prova/ficha e cabeçalhos como `BACKGROUND AND OBJECTIVES` ou `Programa de Mestrado...`.
- **Reconciliação de DOI/ISBN e Ano**:
  - `confirmar_mesma_obra` corrobora o título da API pela amostra normalizada ou pelo nome do arquivo; títulos de fontes oficiais confirmadas não geram aviso de título inválido; diferença de ano <= 2 anos ("mantido ano explícito") deixa de ser conflito irreconciliável.

## [0.5.0] - 2026-10-28

### Adicionado
- **Árvore Padrão de 3 Níveis**:
  - Organização regular em `<Assunto Principal>/<Especificação>/<Plural do Tipo>/` (ex.: `Psicologia/Neuropsicologia/Livros/`).
- **Motor Taxonômico Ponderado Multi-Domínio**:
  - Novo módulo `taxonomia.py` (`CATALOGO_TAXONOMICO`, `MACRO_CDD`) e reescrita de `infer_thematic_area` em 10 macroáreas e 65 especificações (55+), com pontuação por evidências: título/subtítulo (+15), assunto interno CIP (+12), assuntos externos (+8), CDD/CDU (+10, macroárea +4), autores canônicos (+8) e amostra textual (teto +6). Subáreas específicas vencem guarda-chuvas.
- **Extração Completa de Assuntos e CDD da CIP**:
  - `parse_page_cip` passa a extrair os assuntos e a CDD/CDU da ficha catalográfica.
- **Coleta de `subjects` das 5 APIs com Tradução de Aliases em Inglês**:
  - Assuntos retornados pelas APIs externas alimentam o motor; descritores em inglês são traduzidos via `ALIASES_ASSUNTOS_INGLES` (usado apenas em assuntos, nunca no corpo do texto).
- **Amortecimento do Co-assunto "Psiquiatria"**:
  - O termo genérico "psiquiatria" (e CDD `616.89*` específicas de psicoterapia/TCC/psicodiagnóstico) deixa de sequestrar obras de Psicologia quando o título não o declara.
- **Equivalência Semântica JEV**:
  - `_areas_jev_compativeis` reconhece macroáreas equivalentes (ex.: *Computação* ≡ *Ciência da Computação*) em `compatibilidade_jev_assunto`.
- **Frontmatter YAML 2.0**:
  - `area_principal` e `subarea` sempre persistidos (nunca nulos).

### Alterado
- Normalização textual (`_norm_ascii`) trata hífens, underscores e barras como espaços; `_contar_termos` deduplica singular/plural.

## [0.4.4] - 2026-10-27

### Corrigido
- **Eliminação de Congelamento por Header `Retry-After` (OpenAlex 429)**:
  - Configurado `respect_retry_after_header=False` no adaptador de Retry do `urllib3`, impedindo que respostas HTTP 429 com cabeçalhos `Retry-After` excessivos bloqueiem a execução em chamadas de rede externas.
- **Higienização de Underscores em Nomes de Arquivo**:
  - Conversão automática de underscores (`_`) em espaços em `parse_filename_extended` e `sanitize_title_candidate`, impedindo que títulos como `Casos_Clínicos_em_Saúde_Mental` sejam tratados erroneamente como palavras únicas.
- **Descarte de Cabeçalhos de Citação de Periódicos**:
  - Bloqueio sistemático de expressões de paginação e fascículo (`v. \d+, n. \d+, p. \d+-\d+`) e cabeçalhos de revistas acadêmicas como títulos de artigos.
- **Rejeição de Entidades Institucionais de Fichas CIP**:
  - Bloqueio de expressões como *Câmara Brasileira do Livro* e *Sindicato Nacional dos Editores* como títulos válidos.

## [0.4.3] - 2026-10-27

### Adicionado
- **Reconciliação Canônica de ISBN sem Falso Conflito**:
  - Divergências informativas entre texto heurístico de OCR e o registro canônico oficial (Brasil API / CBL) são tratadas como reconciliação canônica, evitando quarentena indevida para livros com ISBN oficial confirmado (resolvendo caso de *A Síndrome do Impostor*).
- **Descarte de Disclaimers de Digitalização e Crowdfunding**:
  - Adicionada detecção estrita de disclaimers de projetos de digitalização coletiva (*DADOS DE ODINRIGHT*, *eLivros*, *ePubtoPDF*) e pedidos de doação/custeio (*"servidores"*, *"compramos para postar"*, *"faça uma doação/pix"*).
  - Pulo automático de páginas de abertura que contenham tais disclaimers, fazendo o extrator avançar diretamente para a folha de rosto canônica (resolvendo caso de Carl Rogers: *De Pessoa para Pessoa*).
  - Normalização Unicode `NFKD` para neutralizar ligaduras tipográficas (ex.: `ﬁ` -> `fi`).
- **Filtragem de Autores Técnicos de PDF Metadata**:
  - Descarte sistemático de operadores de diagramação e nomes de softwares em propriedades internas do PDF (`Daidigital`, `Alfred`, `PageMaker`, `PDFCreator`, etc.).
  - Priorização de `fn_author` higienizado do nome do arquivo quando a propriedade do PDF for genérica ou unipalavra.
- **Higienização de Sufixo `et al` nos Metadados e Formatação ABNT**:
  - Remoção automática de `et al` ao processar nomes de arquivos e propriedades, evitando a duplicação `AL, Sobrenome et et al.` e gerando citações limpas e canônicas.
- **Bloqueio de Cabeçalhos de Fascículo e Periodicidade**:
  - Bloqueio estrito de expressões de volume, fascículo e datas (`VOLUME \d+`, `NÚMERO \d+`, `DEZEMBRO 1999`, `MÊS ANO`), permitindo o resgate do título real do artigo (resolvendo caso de *Gonçalves et al*).
- **Suíte de Testes Expandida**:
  - Total de **313 testes automatizados** (100% de aprovação na suíte completa).

## [0.4.2] - 2026-10-27

### Adicionado
- **Extração Resiliente de ISBN**:
  - Suporte completo a variações gráficas e rotulações editoriais (`I.S.B.N.`, `I. S. B. N.`, `ISBN 10`, `ISBN-13`, quebras de linha `ISBN:\n...` e especificadores como `ISBN (impresso):`, `ISBN (versão digital):`, `ISBN (brochura):`).
  - Suporte nativo a hífens unicode (`–` en-dash, `—` em-dash, `\u2212`) e espaços não-quebráveis (`\xa0`).
  - Extração de ISBNs autônomos de 13 dígitos iniciados por 978 ou 979 no corpo do texto e colofão.
- **Extração Universal de ISSN**:
  - Suporte a `e-ISSN`, `p-ISSN`, `ISSN-L`, `ISSN online`, `ISSN impresso` e quebras de linha em periódicos científicos nacionais e internacionais.
- **Extração Segura de DOI em Periódicos**:
  - Remoção de falsos descartes em cabeçalhos de artigos legítimos que trazem paginação e fascículo (`v.`, `n.`, `p.`) adjacentes ao identificador.
- **Hierarquia Estrita de Autoridade Bibliográfica**:
  - Identificadores matematicamente validados e confirmados em fontes oficiais (Brasil API / CBL, Crossref, DataCite, Google Books) sobrepõem e corrigem automaticamente quaisquer títulos, subtítulos ou autores heurísticos ruidosos extraídos do PDF.
- **Fallback Dirigido pelo Nome do Arquivo**:
  - Recuperação progressiva de registros bibliográficos via busca dirigida nas APIs oficiais usando o título e autor inferidos do nome do arquivo quando o PDF não contém identificador e apresenta texto com características de epígrafe ou disclaimer.
- **Filtros Reforçados contra Falsos Títulos**:
  - Rejeição de epígrafes poéticas/devocionais ("Segundo a filosofia budista...", "devem ser acessíveis...").
  - Rejeição de entidades comerciais com ano ("literare books international ltda, 2021.").
  - Rejeição de rótulos isolados da catalogação (`(CIP)`, `CIP-BRASIL`, `CDD`, `CDU`, `Título original:`, `Titulooriginalem alemao`).
  - Rejeição de redes sociais, emails e URLs como títulos.
- **Suíte de Testes Expandida**:
  - Inclusão do módulo `tests/test_assertividade_identificadores.py`, totalizando **309 testes automatizados** (100% de aprovação).

## [Não Lançado]

### Adicionado
- **Separação de Confiança de Classificação e Metadados**: Criação dos campos `confidence_classification` e `confidence_metadata` (além da persistência no Markdown companion). Documentos com alta certeza de tipo ainda são roteados para revisão manual se título, autoria ou identificadores apresentarem anomalias.
- **Rastreabilidade de Fontes por Campo (Provenance)**: Registro explícito das fontes de evidência (`title_source`, `author_source`, `isbn_source`, `issn_source`, `doi_source`) no modelo e no frontmatter YAML do Markdown (`texto_nativo`, `cip`, `nome_arquivo`, `api_crossref`, etc.).
- **Tratamento Especializado de PDFs Digitalizados**: Detecção de arquivos sem texto nativo como `Situacao.PENDENTE_OCR` e `requer_ocr=True`, exibidos em tabela separada das falhas técnicas de execução.
- **Suporte Oficial à Instalação TypeSafe**: Suporte formal via extra `pip install -e ".[typesafe]"` e validação estrita sem fallback silencioso no modo remoto.
- **Higienização e Filtragem Estrita de Títulos**:
  - Rejeição completa de identificadores (ISSN, ISBN, DOI) como títulos de documentos.
  - Descarte de disclaimers de direitos autorais / domínio público (caso Cida Bento).
  - Truncamento e limpeza de blocos de créditos editoriais (caso Angela Ales Bello).
  - Limpeza de numeração de páginas residuais e códigos de classificação CDD/CDU/Cutter (caso Neuropsicologia).
  - Quarentena obrigatória para unipalavras/verbos genéricos (caso bell hooks) e categoria "Outros".
- **Transparência de Rede na Interface**: Separação clara entre a transmissão do classificador remoto (JEV/TypeSafe) e as consultas bibliográficas públicas, eliminando a exibição ambígua de "Modo Offline" quando há tráfego de rede ativo.
- **Teste de Retomada Idempotente pós-Falha de Persistência**: Garantia de reutilização do cache remoto sem cobrança duplicada caso ocorra falha de escrita local.

## [0.4.0] - 2026-10-03

### Adicionado

- **Motor determinístico local e inteligente (CIP / Ficha Catalográfica + Heurísticas Jev)**:
  Substituição completa da dependência de LLMs pesados/pagos por extração rápida,
  local e livre de custos via parsing de Ficha Catalográfica (padrões AACR2 e ISBD)
  e heurísticas bibliográficas especializadas (`classifier_jev.py`).
- **Validação e Enriquecimento Multi-API Pública**:
  Integração automática e gratuita com Brasil API (Câmara Brasileira do Livro / ISBN),
  Google Books, Crossref (DOI), OpenAlex e OpenLibrary (`metadata_api.py`), com
  validação de similaridade entre metadados extraídos e metadados retornados.
- **Formatação Rigorosa ABNT NBR 6023:2018**:
  Módulo dedicado (`abnt_formatter.py`) com manipulação adequada de autores em caixa-alta,
  sobrenomes compostos em língua portuguesa e espanhola, e preservação de agnomes
  familiares (Filho, Júnior, Neto, Sobrinho).
- **Regra de Conversão de Markdown Companheiro**:
  Amostragem inteligente das 10 primeiras e 10 últimas páginas para metadados,
  sem conversão redundante ou custosa do corpo integral do PDF para Markdown. O
  arquivo `.md` gerado contém YAML frontmatter rico, referência ABNT e metadados
  de catalogação.
- **Nomenclatura Visual Padronizada**:
  Renomeação limpa e consistente no formato `SOBRENOME, Nome - Título (Ano)`.
- **Filtro de Disclaimers Universitários**:
  Identificação e descarte automático de termos institucionais de repositórios digitais
  (ex.: "Este exemplar foi revisado...", "Todos os direitos reservados à...", etc.)
  para impedir alucinação na identificação de autoria e título da obra.

### Removido

- Dependência de LLMs externos e serviços locais de inferência (`ollama`, `anthropic`).
- Módulos obsoletos de provedores LLM (`provedores.py`).
- Dependência pesada de conversão completa de layout (`pymupdf4llm`).

## [0.3.0] - 2026-08-19

### Adicionado

- Truncamento dinâmico de nome de arquivo para caber no limite seguro de
  caminho do Windows (MAX_PATH): o orçamento de caracteres agora leva em
  conta o comprimento real de `--destino` (livre, não controlado pelo app),
  não só um teto fixo por segmento. Se nem um nome mínimo couber, falha com
  mensagem clara em vez de tentar gravar um caminho inválido.
- Busca por ficha catalográfica além da janela inicial de páginas: quando
  prefácio/dedicatória/sumário longos empurram ISBN/ISSN/DOI/"ficha
  catalográfica" para fora das `--max-paginas` páginas enviadas ao LLM, uma
  busca leve (texto simples, sem custo de LLM) nas páginas seguintes acha
  essa página e a inclui na análise, com orçamento de caracteres reservado
  para não ser cortada pelo teto de `--max-caracteres`.
- Enriquecimento de metadados via verificação online: quando o ISBN/DOI é
  confirmado contra Crossref/Open Library (dados batendo, mesma obra),
  `editora_ou_periodico`/`ano`/`local` que o LLM deixou em branco são
  preenchidos com o que a API devolveu — nunca sobrescrevendo um valor já
  extraído, e só quando a verificação já confirmou que é a mesma obra.
- `--paralelo`/`-j`: processa N arquivos ao mesmo tempo (threads). Padrão 1
  (sequencial, comportamento inalterado) — só compensa com provedor pago
  remoto, já que o Ollama local não ganha nada rodando em paralelo. Uma
  falha fatal de API para de puxar trabalho novo imediatamente (janela
  deslizante, não dispara tudo de uma vez); gravação em disco e no estado
  do `--resume` são serializadas para evitar corrida entre threads.

## [0.2.0] - 2026-08-18

### Adicionado

- Manual de instalação por sistema operacional (macOS, Linux, Windows) no
  README, separando os passos de baixar a CLI e configurar o Ollama, com as
  ressalvas específicas de cada SO (Gatekeeper, SmartScreen, systemd).
- Suporte opcional a provedores pagos (Anthropic, OpenAI, DeepSeek, Gemini,
  Grok), escolhidos explicitamente via `--provedor`/`ORGPDF_PROVEDOR` — o
  padrão continua sendo o Ollama local. `--modelo`/`-m` passa a aceitar
  qualquer modelo do provedor escolhido, e `--apikey`/`-k` (ou
  `ORGPDF_<PROVEDOR>_API_KEY`/`ORGPDF_API_KEY`) autentica a chamada. As
  proteções contra alucinação (aviso de divergência, descarte de
  identificador não confirmado) valem para todo provedor, pago ou não.
- Fluxo de branches: desenvolvimento passa a acontecer em `develop`, com
  merge para `main` e changelog atualizado a cada release validado.
- Fallback opcional para um provedor pago (`--provedor-fallback`/
  `--modelo-fallback`/`--apikey-fallback`, ou `ORGPDF_PROVEDOR_FALLBACK`/
  `ORGPDF_MODELO_FALLBACK`): quando o resultado do provedor principal sai com
  aviso (divergência de nome de arquivo, identificador não confirmado, ou
  verificação online) ou a extração falha, o app tenta de novo com o
  provedor de fallback antes de desistir do arquivo. Desligado por padrão —
  sem `--provedor-fallback`, nenhuma chamada extra é feita.
- Qualquer arquivo que saia com aviso (de qualquer proteção, mesmo depois do
  fallback) agora é gravado em `<destino>/revisao_manual/<área>/<subárea>/
  <tipo>/` em vez da árvore normal, mantendo a mesma organização mas isolada
  para facilitar a revisão manual.
- `--resume`: retoma um lote interrompido (Ctrl+C, queda, Ollama fora do ar,
  chave de API inválida) sem precisar repetir `--origem`/`--destino`/demais
  opções — elas são reaplicadas automaticamente a partir da execução
  anterior. Pula arquivos já concluídos (sucesso ou falha definitiva); só o
  que ficou pra trás pela interrupção em si é retomado. Progresso salvo
  incrementalmente em `~/.organizador-pdf/estado.json`, sem nunca persistir
  `--apikey`/`--apikey-fallback`; limpo automaticamente quando um lote
  termina por completo.
- Registro de quando o provedor de fallback foi de fato usado (não só
  tentado): marcador `$` na tabela de metadados do terminal, campos
  `provedor_extracao`/`extraido_via_fallback` no frontmatter de cada `.md`
  gerado, e uma contagem "extraído(s) localmente vs. via provedor pago" no
  resumo final do lote.
- `--max-paginas`/`--max-caracteres`: até então só existiam como
  `ORGPDF_MAX_PAGINAS`/`ORGPDF_MAX_CARACTERES` no `.env`, únicos parâmetros
  sem flag de linha de comando equivalente — agora seguem o mesmo padrão de
  precedência (CLI > variável de ambiente > padrão) do resto da CLI.

### Modificado

- `pymupdf4llm` passa a exigir `>=1.28` (era `>=0.0.17`) — versão mínima
  com `pymupdf4llm.ocr.OCRMode`, usado só para desligar explicitamente o OCR
  automático da biblioteca (este app não faz OCR; PDFs digitalizados/
  escaneados falham com mensagem explícita — veja o README).

### Corrigido

- **Binário standalone (PyInstaller) silenciosamente sem o motor de layout
  do `pymupdf4llm` desde o v0.1.0**: os modelos ONNX que `pymupdf` carrega
  do disco em tempo de execução (`layout/resources/`, ~49 MB) não eram
  coletados pela análise estática do PyInstaller — o binário caía sem erro
  nenhum para extração de texto simples (sem estrutura de título/tabela) em
  **todo** PDF processado. Corrigido coletando os dados desse pacote no spec
  (`packaging/organizador-pdf.spec`). Quem já baixou um binário do v0.1.0 nas
  Releases está rodando com essa limitação até a próxima release.

### Corrigido

- Extração via DeepSeek falhava em todo arquivo com erro 400 ("This
  response_format type is unavailable now"): a API deles rejeita
  `response_format: json_schema` estrito, só aceita `json_object`. O
  adaptador OpenAI-compatível agora detecta esse caso e usa o modo certo por
  provedor, incluindo a palavra "json" e um exemplo de formato no prompt
  (exigência documentada da própria DeepSeek para o modo `json_object`).

## [0.1.0] - 2026-08-17

### Adicionado

- Primeira versão: conversão de PDF para Markdown estruturado
  (`pymupdf4llm`), extração de metadados bibliográficos via LLM local
  (Ollama, sem provedores pagos) e organização automática em
  `<destino>/<área>/<subárea>/<tipo>/`.
- Referência bibliográfica no padrão ABNT NBR 6023 e YAML frontmatter
  compatível com Obsidian no Markdown gerado.
- Proteções determinísticas contra alucinação do modelo: aviso de
  divergência entre título/autor extraído e nome do arquivo original, e
  descarte de ISBN/ISSN/DOI que não aparecem literalmente no texto-fonte.
- Verificação online opcional de ISBN/DOI contra Crossref e Open Library
  (`ORGPDF_VERIFICAR_ONLINE`), única exceção ao funcionamento 100% offline.
- Empacotamento multiplataforma via PyInstaller, com build e publicação
  automática de binários standalone (macOS arm64, Linux, Windows) no GitHub
  Actions a cada tag `v*`.

### Corrigido

- Corrompimento de identificador confirmado (ex.: DOI) quando um
  identificador fabricado não confirmado (ex.: ISSN) era, por coincidência,
  uma substring dele — a remoção passou a exigir fronteira de palavra (`\b`)
  em vez de substring solta.
- Rename do binário Windows na pipeline de release: `Rename-Item` do
  PowerShell não aceita um caminho no novo nome, só o nome do arquivo.
