# Organizador e Catalogador Inteligente de PDFs — Especificação

> **Documento de Referência Técnica e Especificação do Sistema**  
> **Versão:** 0.4.1 (Remediada pós-auditoria bibliográfica e alinhada às regras do CNPq)  
> **Status:** 100% Testado (301 testes automatizados aprovados)  
> *Consulte [Prompt.md](Prompt.md) como a Fonte Única da Verdade e Prompt Mestre de Recriação.*

---

## 1. Visão Geral

CLI multiplataforma (`organizador-pdf`) que processa um lote de PDFs acadêmicos e editoriais e, para cada um:

1. Realiza amostragem leve de texto nativo (primeiras 10 e últimas 10 páginas) sem OCR nem conversão do corpo/miolo do livro;
2. Extrai metadados bibliográficos e classifica a obra segundo a tipologia documental e a **Tabela de Áreas do Conhecimento do CNPq** (Grandes Áreas e Subáreas);
3. Valida e enriquece os metadados através de consultas gratuitas a APIs públicas (Brasil API / CBL, Google Books, Crossref, DataCite, OpenAlex e OpenLibrary);
4. Formata a referência bibliográfica em estrita conformidade com a norma **ABNT NBR 6023:2018**;
5. Gera um arquivo Markdown companheiro (`.md`) com frontmatter YAML canônico 2.0 e citação ABNT para catalogação bibliográfica estruturada (sem o texto original do miolo do PDF);
6. Renomeia o PDF no padrão acadêmico canônico `SOBRENOME, Nome - Título (Ano).pdf` e organiza os arquivos em uma árvore de diretórios hierárquica baseada nas áreas do CNPq (`<destino>/<Grande Área>/<Subárea>/<Plural do Tipo>/`), com isolamento em `revisao_manual/` para casos de incerteza de identidade ou integridade.

Filosofia central: **Determinístico, ágil, de alta precisão biblioteconômica e de custo controlado**. Classificador padrão via TypeSafe AI (Jev Remoto com envio máximo de 3.000 caracteres de abertura, sem envio de PDF) com fail-fast, além de motor determinístico local offline completo (`--classificador local`).

---

## 2. Regras de Negócio

### 2.1. Fidelidade Bibliográfica e Determinismo
- **Ficha Catalográfica (CIP) como verdade primária**: quando presente no bloco inicial da obra (padrões AACR2 ou ISBD), as informações catalográficas registradas por bibliotecários têm precedência máxima sobre heurísticas genéricas.
- **Filtro de disclaimers e repositórios**: termos institucionais comuns de repositórios universitários ("Este exemplar foi revisado...", "Todos os direitos reservados à...") são explicitamente ignorados na identificação de autoria e título da obra.
- **Validação matemática de identificadores**: ISBN-10 (módulo 11 com conversão para ISBN-13), ISBN-13 (módulo 10) e ISSN-8 (módulo 11 com 'X') são validados por checksums estritos. DOIs são normalizados.
- **Similaridade em enriquecimento**: se uma API externa retornar dados para um ISBN ou DOI, os campos ausentes só são enriquecidos se houver compatibilidade comprovada (similaridade de títulos e autores) entre os dados extraídos do documento e os dados retornados pela API.
- **Roteamento para revisão manual**: acionado apenas por critérios bibliográficos reais (incerteza de identidade, tipologia duvidosa, conflito irreconciliável ou integridade violada). Avisos informativos como ausência de cidade (`[S. l.]`) não geram quarentena.

### 2.2. Leveza, Privacidade e Sem Conversão do Miolo
- O processamento de cada documento é instantâneo (frações de segundo).
- **Sem conversão de corpo**: o arquivo `.md` companheiro não contém o texto convertido do livro nem seções de conteúdo.
- O arquivo PDF original **NUNCA** trafega pela rede. No modo remoto, apenas até 3.000 caracteres de texto nativo de abertura são enviados.
- O acervo é delimitado a documentos acadêmicos e editoriais (sem dados sensíveis ou prontuários).

### 2.3. Resiliência por Arquivo e Persistência de Estado (`--resume`)
- Falhas em um arquivo individual (arquivo corrompido, protegido por senha ou sem camada de texto nativa) nunca interrompem o processamento do restante do lote.
- O progresso é persistido incrementalmente em `~/.organizador-pdf/estado.json`. Em caso de interrupção forçada (Ctrl+C ou queda do sistema), a flag `--resume` retoma a execução exatamente dos arquivos pendentes, sem reprocessar arquivos já concluídos.
- Conclusão completa do lote limpa automaticamente o arquivo de estado salvo.

### 2.4. Nomenclatura e Organização Conforme Regras do CNPq
- **Padrão de nomenclatura**: `SOBRENOME, Nome - Título (Ano).pdf` (e `.md`).
- **Estrutura de diretórios CNPq**: `<DESTINO>/<Grande Área CNPq>/<Subárea CNPq>/<Plural do Tipo>/` (padrão oficial `--estrutura cnpq`).
- **Respeito aos limites do sistema operacional (MAX_PATH)**: o tamanho do caminho completo é monitorado dinamicamente para garantir compatibilidade segura com o teto de 260 caracteres do Windows. Se necessário, o título é truncado de forma elegante mantendo extensão e identificadores essenciais.
- **Prevenção de colisões**: arquivos existentes no destino recebem sufixos numéricos sequenciais ` (2)`, ` (3)` sem sobrescrever dados prévios.

### 2.5. Sem OCR Embutido
Documentos puramente escaneados ou digitalizados como imagem pura falham com aviso informativo claro (`Situacao.PENDENTE_OCR` e `requer_ocr=True`), instruindo o usuário a aplicar pré-processamento via ferramentas especializadas de OCR (ex.: `ocrmypdf`).

---

## 3. Requisitos Funcionais (RF)

* **RF1 — Interface CLI:** Typer com flags: `--origem` / `-i`, `--destino` / `-o`, `--estrutura` (`cnpq` | `plana`), `--plana`, `--classificador` (`remoto` | `local`), `--dry-run`, `--resume`, `--mover`, `--subpasta-md`, `--paralelo` / `-j`, `--quarantine`, `--interactive`, `--limite` / `-n`, `--log`, `--verbose` / `-v`.
* **RF2 — Amostragem de Texto Nativo:** Módulo `converter.py` extrai até 10 primeiras páginas e 10 últimas páginas via PyMuPDF. Detecta páginas com menos de 30 caracteres válidos como indicativo de página gráfica/escaneada.
* **RF3 — Classificação Documental e Taxonomia CNPq:** Módulo `classifier_jev.py` e integração com TypeSafe AI / Jev. Classificação de tipologia e enquadramento nas Grandes Áreas e Subáreas da Tabela do CNPq.
* **RF4 — Consulta e Enriquecimento Multi-API:** Módulo `metadata_api.py` consultando Brasil API / CBL, Google Books, Crossref, DataCite, OpenAlex e OpenLibrary com checagem de similaridade e rastreabilidade de cache em disco vs rede ao vivo.
* **RF5 — Formatação ABNT NBR 6023:2018:** Módulo `abnt_formatter.py` com autores em caixa-alta, sobrenomes compostos, agnomes familiares (Filho, Júnior, etc.), `[S. l.]` e `[s. n.]`, e omissão de ISBN na referência de livros.
* **RF6 — Companion Markdown Neutro:** Geração de `.md` companheiro com YAML Frontmatter 2.0 e referência ABNT em bloco de citação. Sem o corpo do PDF e sem amarras a plataformas específicas.
* **RF7 — Nomenclatura e Diretórios CNPq:** Nomes no padrão acadêmico e organização em árvore CNPq com salvaguarda `MAX_PATH` do Windows e isolamento em `revisao_manual/`.
* **RF8 — Auditoria e Rastreabilidade:** Geração de `<nome>.auditoria.json` e relatório executivo em PDF formal (`scripts/gerar_relatorio_pdf.py`).

---

## 4. Requisitos Não Funcionais (RNF)

- **RNF1 — Linguagem e Tipagem:** Python 3.10+, anotações de tipo completas (`typing`) e modelos estruturados via Pydantic v2 e Dataclasses.
- **RNF2 — Desempenho:** Processamento médio inferior a 200ms por documento em modo sequencial e inferior a 50ms por documento em modo paralelo (4 threads).
- **RNF3 — Confiabilidade e Autonomia:** Operação remota oficial e modo determinístico local offline completo via `--classificador local`.
- **RNF4 — Interface e UX:** Visualização rica via `rich` com tabelas de metadados, árvores de diretórios, barras de progresso e transparência de rede.
- **RNF5 — Cobertura de Testes:** Suíte completa com 100% dos testes passando sem internet (301 testes automatizados via `pytest`).

---

## 5. Estrutura do Código-Fonte

Consulte a seção 5 de [Prompt.md](Prompt.md) para a árvore completa de diretórios e arquivos do projeto.

---

## 6. Prompt Mestre de Recriação

Consulte a seção 6 de [Prompt.md](Prompt.md) para o prompt mestre e completo de recriação do sistema.
