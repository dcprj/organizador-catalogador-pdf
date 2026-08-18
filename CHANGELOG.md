# Changelog

Todas as mudanças notáveis deste projeto são documentadas aqui.

O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o
projeto adota [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [Não Lançado]

### Adicionado

- Manual de instalação por sistema operacional (macOS, Linux, Windows) no
  README, separando os passos de baixar a CLI e configurar o Ollama, com as
  ressalvas específicas de cada SO (Gatekeeper, SmartScreen, systemd).

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
