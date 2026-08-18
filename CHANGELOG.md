# Changelog

Todas as mudanças notáveis deste projeto são documentadas aqui.

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o
projeto adota [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [Não Lançado]

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
- OCR automático para PDFs digitalizados (`--ocr`/`--no-ocr`,
  `--ocr-idioma`, ou `ORGPDF_OCR`/`ORGPDF_OCR_IDIOMA`), usando o suporte
  nativo do `pymupdf4llm` a Tesseract — sem configuração extra: se o
  Tesseract estiver instalado, é usado automaticamente; sem ele, o
  comportamento continua o de sempre (falha explícita). Mensagens internas
  do PyMuPDF (status de OCR por página) deixaram de poluir o console.

### Modificado

- `pymupdf4llm` passa a exigir `>=1.28` (era `>=0.0.17`) — versão mínima
  com suporte a OCR nativo (`use_ocr`/`ocr_language`).

### Corrigido

- **Binário standalone (PyInstaller) silenciosamente sem o motor de layout
  do `pymupdf4llm` desde o v0.1.0**: os modelos ONNX que `pymupdf`
  (`layout/resources/`, ~49 MB) e `pymupdf4llm`
  (`ocr/ocr_decision_model.onnx`) carregam do disco em tempo de execução não
  eram coletados pela análise estática do PyInstaller — o binário caía sem
  erro nenhum para extração de texto simples (sem estrutura de
  título/tabela, sem OCR) em **todo** PDF processado, não só nos
  digitalizados. Corrigido coletando os dados desses dois pacotes no spec
  (`packaging/organizador-pdf.spec`); descoberto ao validar o OCR no binário
  de ponta a ponta. Quem já baixou um binário do v0.1.0 nas Releases está
  rodando com essa limitação até a próxima release.

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
