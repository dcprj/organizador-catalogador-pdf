# Changelog

Todas as mudanças notáveis deste projeto são documentadas aqui.

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o
projeto adota [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [Não Lançado]

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
