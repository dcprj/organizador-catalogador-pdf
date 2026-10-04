# Organizador e Catalogador Inteligente de PDFs — Especificação

> Documento de referência do projeto: visão geral, regras de negócio,
> requisitos funcionais e não funcionais, e um prompt de recriação ao final.
> Escrito para sobreviver à perda do repositório — junto com o prompt da
> seção 6, é o suficiente para reconstruir o projeto do zero.

**Versão coberta por este documento:** v0.4.0 (2026-10-03).

---

## 1. Visão geral

CLI multiplataforma (`organizador-pdf`) que processa um lote de PDFs e, para cada um:

1. Realiza amostragem leve de texto nativo (primeiras 10 e últimas 10 páginas) sem OCR nem conversão pesada de corpo inteiro;
2. Extrai metadados bibliográficos de forma determinística via Ficha Catalográfica (CIP nos padrões AACR2 e ISBD) e heurísticas bibliográficas especializadas;
3. Valida e enriquece os metadados através de consultas gratuitas a APIs públicas (Brasil API / CBL, Google Books, Crossref, OpenAlex e OpenLibrary);
4. Formata a referência bibliográfica em estrita conformidade com a norma ABNT NBR 6023:2018;
5. Gera um arquivo Markdown companheiro (`.md`) com frontmatter YAML e referência ABNT para Obsidian/Logseq/Notion;
6. Copia (ou move) o PDF renomeado no padrão visual `SOBRENOME, Nome - Título (Ano)` e organiza os arquivos em uma árvore de diretórios categorizada por tipo documental (com isolamento em `revisao_manual/` para casos com divergência ou baixa confiança).

Filosofia central: **100% determinístico, local, gratuito e instantâneo**. Sem dependências de modelos de linguagem pesados (LLMs locais como Ollama ou APIs pagas como OpenAI/Anthropic), eliminando custos por token, latência de inferência e risco de alucinação sintética.

---

## 2. Regras de negócio

### 2.1. Fidelidade bibliográfica e determinismo

A catalogação bibliográfica exige precisão factual. Para garantir que nenhum autor, título ou identificador seja inventado:

- **Ficha Catalográfica (CIP) como verdade primária**: quando presente no bloco inicial da obra (padrões AACR2 ou ISBD), as informações catalográficas registradas por bibliotecários têm precedência máxima sobre heurísticas genéricas.
- **Filtro de disclaimers e repositórios**: termos institucionais comuns de repositórios universitários ("Este exemplar foi revisado...", "Todos os direitos reservados à...") são explicitamente ignorados na identificação de autoria e título da obra.
- **Validação de identificadores**: ISBN, ISSN e DOI encontrados são validados estruturalmente e confrontados contra bases bibliográficas públicas para confirmar que correspondem exatamente à obra analisada.
- **Similaridade em enriquecimento**: se uma API externa retornar dados para um ISBN ou DOI, os campos ausentes só são enriquecidos se houver compatibilidade comprovada (similaridade de títulos e autores) entre os dados extraídos do documento e os dados retornados pela API.
- **Roteamento para revisão manual**: qualquer documento com baixa confiança heurística, ausência de elementos fundamentais ou divergência de dados é enviado para `<destino>/revisao_manual/<tipo>/` para validação humana.

### 2.2. Leveza, privacidade e custo zero

- O processamento de cada documento é instantâneo (frações de segundo).
- Nenhuma dependência pesada de inferência de IA ou de GPU é necessária.
- As chamadas de rede são estritamente limitadas a consultas HTTP públicas (Brasil API, Google Books, Crossref, OpenAlex, OpenLibrary), transmitindo unicamente o identificador já localizado ou termos de busca do título, sem enviar o documento ou dados sensíveis.
- O pipeline opera com resiliência total a falhas de rede: se uma API estiver inacessível ou sem internet, a extração local continua normalmente.

### 2.3. Resiliência por arquivo e persistência de estado (`--resume`)

- Falhas em um arquivo individual (arquivo corrompido, protegido por senha ou sem camada de texto nativa) nunca interrompem o processamento do restante do lote.
- O progresso é persistido incrementalmente em `~/.organizador-pdf/estado.json`. Em caso de interrupção forçada (Ctrl+C ou queda do sistema), a flag `--resume` retoma a execução exatamente dos arquivos pendentes, sem reprocessar arquivos já concluídos.
- Conclusão completa do lote limpa automaticamente o arquivo de estado salvo.

### 2.4. Nomenclatura e organização em disco

- **Padrão de nomenclatura**: `SOBRENOME, Nome - Título (Ano).pdf` (e `.md`).
- **Respeito aos limites do sistema operacional (MAX_PATH)**: o tamanho do caminho completo é monitorado dinamicamente para garantir compatibilidade segura com o teto de 260 caracteres do Windows. Se necessário, o título é truncado de forma elegante mantendo extensão e identificadores essenciais.
- **Prevenção de colisões**: arquivos existentes no destino recebem sufixos numéricos sequenciais ` (2)`, ` (3)` sem sobrescrever dados prévios.
- **Markdown companheiro**: o arquivo `.md` é gerado como metadado complementar estruturado, evitando a duplicação desnecessária do corpo integral de centenas de páginas de texto do PDF.

### 2.5. Sem OCR embutido

Documentos puramente escaneados ou digitalizados como imagem pura falham com aviso informativo claro, instruindo o usuário a aplicar pré-processamento via ferramentas especializadas de OCR (ex.: `ocrmypdf`).

---

## 3. Requisitos funcionais (RF)

### RF1 — Interface de Linha de Comando (CLI)
Ponto de entrada único via Typer com comandos intuitivos e flags:
- `--origem` / `-i`: diretório com os arquivos PDF a processar (obrigatório, salvo em `--resume`).
- `--destino` / `-o`: diretório de saída para a árvore organizada (obrigatório, salvo em `--resume`).
- `--dry-run`: simulação completa com exibição de tabelas sem escrita em disco.
- `--resume`: retomada de lotes pendentes.
- `--mover`: move os arquivos originais em vez de copiar.
- `--subpasta-md`: direciona os arquivos `.md` companheiros para uma pasta separada espelhada.
- `--paralelo` / `-j`: processamento concorrente multi-threaded.
- `--quarantine`: roteia arquivos duvidosos para `revisao_manual/`.
- `--limite` / `-n`: teto de arquivos a processar na sessão.
- `--log`: especificação do arquivo de registro de erros.
- `--verbose` / `-v`: modo verboso para depuração.

### RF2 — Amostragem de Texto Nativo
- Módulo `converter.py` extrai até 10 primeiras páginas e 10 últimas páginas do PDF via PyMuPDF.
- Detecta páginas com menos de 30 caracteres válidos como indicativo de página gráfica/escaneada.

### RF3 — Extração e Classificação Determinística
- Módulo `classifier_jev.py`:
  - Parser de Ficha Catalográfica (CIP) para extração de título, subtítulo, autor, editora, local, ano, ISBN e CDD/CDU.
  - Heurísticas de classificação estrutural para categorizar em `Livro`, `Artigo Científico`, `Dissertação/Tese`, `Revista/Periódico`, `Apostila` ou `Outros`.
  - Extração de metadados de colofão e páginas finais.

### RF4 — Consulta e Enriquecimento Multi-API
- Módulo `metadata_api.py`:
  - Consulta automática à Brasil API / CBL para validação de ISBNs brasileiros.
  - Consulta a Google Books e OpenLibrary para livros internacionais.
  - Consulta a Crossref e OpenAlex para artigos científicos via DOI ou título.
  - Cálculo de índice de similaridade para mesclagem segura de campos faltantes.

### RF5 — Formatação ABNT NBR 6023:2018
- Módulo `abnt_formatter.py`:
  - Formatação completa de referência em linha única.
  - Sobrenomes de autores em letras maiúsculas.
  - Tratamento gramatical de sobrenomes compostos (ex.: "Espirito Santo", "Villas Boas") e agnomes de parentesco ("Filho", "Júnior", "Neto", "Sobrinho").
  - Formatação tipográfica de títulos e subtítulos com itálico ou negrito conforme tipo de publicação.

### RF6 — Markdown Companheiro e Frontmatter
- Geração de `.md` companheiro com frontmatter YAML contendo:
  - `title`, `author`, `authors`, `year`, `publication_type`, `area`, `subarea`, `isbn`, `issn`, `doi`, `abnt_reference`, `cataloged_at`.
- Corpo do documento contendo título em H1 e referência bibliográfica formatada em bloco de citação para visualização imediata no Obsidian/Logseq.

### RF7 — Organização de Diretórios e Prevenção de Falhas
- Criação automática da taxonomia de pastas `<destino>/<Tipo>/`.
- Encaminhamento automático de anomalias para `<destino>/revisao_manual/<Tipo>/`.
- Validação e saneamento de nomes de arquivo contra caracteres proibidos no Windows, macOS e Linux.

---

## 4. Requisitos não funcionais (RNF)

- **RNF1 — Linguagem e Tipagem**: Python 3.10+, anotações de tipo completas (`typing`) e modelos estruturados via Pydantic e Dataclasses.
- **RNF2 — Desempenho**: processamento médio inferior a 200ms por documento em modo sequencial e inferior a 50ms por documento em modo paralelo (4 threads).
- **RNF3 — Confiabilidade e Autonomia**: zero chamadas a serviços de IA proprietários; funcionamento completo mesmo sem acesso à internet (com fallback automático da camada de API).
- **RNF4 — Interface e UX**: visualização rica via `rich` com tabelas de metadados, árvores de diretórios, barras de progresso interativas e avisos coloridos de status.
- **RNF5 — Cobertura de Testes**: suíte abrangente com 100% dos testes unitários, de regressão e de integração ponta a ponta passando (177 testes automatizados via `pytest`).

---

## 5. Estrutura do Código-Fonte

```
src/organizador_pdf/
  ├── __init__.py           # Versão e exportação de componentes principais
  ├── abnt_formatter.py     # Normas ABNT NBR 6023:2018 e tratamento de nomes
  ├── classifier_jev.py     # Parser CIP / Ficha Catalográfica e Heurísticas
  ├── cli.py                # Interface Typer com Rich e gerenciamento de lote
  ├── config.py             # Configuração e variáveis de ambiente
  ├── converter.py          # Amostragem de texto e geração de markdown companheiro
  ├── estado.py             # Gerenciamento de checkpoint para --resume
  ├── extractor.py          # Definições de exceções e compatibilidade
  ├── logging_utils.py      # Sistema de logs com registro em arquivo e console
  ├── metadata_api.py       # Clientes HTTP multi-API (Brasil API, Crossref, etc.)
  ├── models.py             # Modelos de dados e validação Pydantic unificada
  ├── organizer.py          # Organização de diretórios, MAX_PATH e gravação
  └── pipeline.py           # Orquestração do fluxo determinístico

scripts/
  └── interactive_validator.py # Ferramenta interativa de diagnóstico de extração
tests/                         # Suíte completa com 14 arquivos de testes pytest
```

---

## 6. Prompt de recriação

> Copie o bloco abaixo para recriar o projeto do zero com um assistente de código, caso o repositório seja perdido.

```
Você é um desenvolvedor especialista em Python. Construa uma ferramenta CLI multiplataforma (macOS/Linux/Windows) chamada "organizador-pdf" que processa um lote de PDFs e, para cada um:
1. Extrai o texto das 10 primeiras e 10 últimas páginas usando PyMuPDF (sem OCR, sem conversão integral do corpo de texto).
2. Extrai metadados bibliográficos de forma determinística via parser de Ficha Catalográfica (CIP nos padrões AACR2 e ISBD) e heurísticas especializadas (classificador para Livro, Artigo, Tese/Dissertação, Revista, Apostila).
3. Consulta e enriquece metadados via APIs públicas gratuitas (Brasil API/CBL, Google Books, Crossref, OpenAlex, OpenLibrary), conferindo similaridade antes de mesclar.
4. Formata a referência bibliográfica em estrita conformidade com a ABNT NBR 6023:2018 (autores em caixa-alta, sobrenomes compostos, agnomes familiares como Filho, Júnior, Neto, Sobrinho).
5. Gera um arquivo Markdown companheiro (.md) com YAML frontmatter rico e referência ABNT para Obsidian.
6. Renomeia os arquivos no padrão "SOBRENOME, Nome - Título (Ano)" com salvaguarda MAX_PATH do Windows e os organiza em subdiretórios categorizados por tipo (com envio a revisao_manual/ para casos de baixa confiança).
7. Suporta flags: --origem, --destino, --dry-run, --resume, --paralelo, --mover, --subpasta-md, --quarantine, --verbose.
8. Implemente suíte de testes unitários com pytest com cobertura completa sem dependência de rede externa.
```
