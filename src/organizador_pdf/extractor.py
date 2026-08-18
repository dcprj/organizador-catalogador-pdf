"""Extração de metadados via LLM local (Ollama) e validação com Pydantic.

Requer o Ollama instalado e rodando (https://ollama.com) e o modelo baixado
com `ollama pull <modelo>`. Veja o README para instruções completas. Este
aplicativo não usa nenhum provedor pago — tudo roda na própria máquina.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from typing import Any, Optional

import httpx
from pydantic import ValidationError

from .config import Config
from .converter import DocumentoConvertido
from .models import Identificadores, Metadados

logger = logging.getLogger(__name__)

#: O Ollama demora bastante em CPU/RAM modesta; a análise é só do trecho
#: inicial do PDF, mas ainda assim vale dar folga generosa.
TIMEOUT_PADRAO = 180.0

PROMPT_SISTEMA = """\
Você é um bibliotecário catalogador especialista em normalização bibliográfica \
segundo a ABNT NBR 6023.

Você recebe o trecho inicial de um documento (capa, folha de rosto, ficha \
catalográfica e primeiras páginas) já convertido para texto/Markdown, e devolve \
os metadados bibliográficos estruturados.

REGRA MAIS IMPORTANTE — NUNCA fabrique uma citação plausível. Isto vale para \
TODOS os campos, não só ISBN/DOI/ISSN/ano/editora: se o trecho fornecido não \
tiver uma capa, folha de rosto, cabeçalho de artigo ou ficha catalográfica \
claros identificando A PRÓPRIA obra, é preferível devolver `titulo` como o \
melhor palpite factual (ex.: baseado no nome do arquivo, se fizer sentido) e \
`autores: []`, `editora_ou_periodico: null`, `identificadores` todos null, do \
que inventar autor, revista, editora, ISBN, ISSN ou DOI plausíveis mas \
inexistentes no texto. Uma referência incompleta é muito melhor que uma \
referência errada e completa — o usuário confia nesses dados para citar as \
obras.

Regras:
1. A identidade da obra (título, autores, editora/revista) vem SOMENTE de onde \
o próprio documento se identifica: capa, folha de rosto, cabeçalho do artigo, \
running head, ficha catalográfica. NUNCA extraia título/autor/editora de uma \
obra apenas mencionada, discutida, resenhada ou citada dentro do corpo do \
texto ou das referências bibliográficas — isso descreve OUTRO trabalho, não o \
documento que você está catalogando. Se o trecho é só corpo de texto corrido \
(ex.: um capítulo isolado, sem folha de rosto visível), não há como confirmar \
a identidade da obra com segurança: prefira campos incertos/null a uma \
citação inventada, mesmo que o assunto do texto sugira um tema.
2. Extraia apenas o que estiver evidenciado no documento ou for inferível com \
alta confiança. Nunca invente ISBN, DOI, ISSN, ano ou editora — use null.
3. `titulo` e `subtitulo` são campos separados: se a capa traz \
"Título: subtítulo", divida-os. Não repita o subtítulo dentro do título. \
Ignore números soltos que sejam ruído de conversão (número de página, \
cabeçalho/rodapé misturado ao texto) — não os inclua no título/subtítulo.
4. Nomes de autores SEMPRE no formato bibliográfico "Sobrenome, Nome" em \
`autores` e `autor_principal` (ex.: "Frankl, Viktor E.") — nunca no formato \
natural "Nome Sobrenome". Dentro de `referencia_abnt` o sobrenome fica em \
MAIÚSCULAS (ver regra 6); fora dali, capitalização normal de nome próprio. \
Sobrenomes compostos com "de", "da", "dos" ficam junto ao sobrenome (ex.: \
"Machado de Assis, Joaquim Maria", e "MACHADO DE ASSIS, Joaquim Maria" dentro \
da referência) — o sobrenome de família fica por inteiro antes da vírgula. \
Em `autores` entram só quem escreveu a obra (ou organizadores, quando a obra \
é uma coletânea sem autor único). Tradutor ("tradução de"), revisor \
("revisão científica/técnica") e prefaciador NÃO são autores — não os inclua \
em `autores` nem em `autor_principal`, mesmo que apareçam na folha de rosto.
5. `area_principal` e `subarea` devem usar vocabulário acadêmico consagrado em \
português, capitalizado. A subárea precisa ser mais específica que a área; se \
não houver especificidade identificável, repita a área principal.
6. `referencia_abnt` deve seguir a NBR 6023 conforme o tipo, sempre com o(s) \
sobrenome(s) em maiúsculas e em uma única linha:
   - Livro: SOBRENOME, Nome. **Título**: subtítulo. Edição. Local: Editora, ano.
   - Artigo: SOBRENOME, Nome. Título do artigo. **Nome do Periódico**, Local, \
v. X, n. Y, p. Z-W, ano.
   - Dissertação/Tese: SOBRENOME, Nome. **Título**. Ano. Tipo (Grau em Área) — \
Instituição, Local, ano.
   Use s.l. (local desconhecido), s.n. (editora desconhecida) e s.d. (data \
desconhecida) quando aplicável. NUNCA use formato APA (autor entre \
parênteses, "(ano)."); o formato é sempre ABNT, sobrenome primeiro.
7. Se o documento estiver em outro idioma, mantenha título e subtítulo no \
idioma original, mas classifique área e subárea em português.

Exemplos de formatação correta:

Entrada: capa de um livro com "Em Busca de Sentido — Um psicólogo no campo de \
concentração", autor "Viktor E. Frankl", "Editora Vozes — Petrópolis — 2019".
Saída correta (campos relevantes):
  autores: ["Frankl, Viktor E."]
  autor_principal: "Frankl, Viktor E."
  referencia_abnt: "FRANKL, Viktor E. Em busca de sentido: um psicólogo no \
campo de concentração. Petrópolis: Editora Vozes, 2019."

Entrada: artigo de "Ana Souza" e "Bruno Lima", título "Aprendizado de máquina \
aplicado a diagnóstico", "Revista Brasileira de Computação, v. 12, n. 3, 2021".
Saída correta (campos relevantes):
  autores: ["Souza, Ana", "Lima, Bruno"]
  autor_principal: "Souza, Ana"
  referencia_abnt: "SOUZA, Ana; LIMA, Bruno. Aprendizado de máquina aplicado a \
diagnóstico. Revista Brasileira de Computação, v. 12, n. 3, 2021."

Exemplo do que NÃO fazer: trecho é um capítulo avulso de livro, sem folha de \
rosto, discorrendo sobre a história da psiquiatria — sem nenhuma capa, autor \
ou editora visíveis no texto.
Saída INCORRETA (fabricar uma citação plausível a partir do assunto):
  titulo: "O Internamento e a Constituição da Loucura na História da \
Psiquiatria" / autores: ["Silva, Ana"] / editora_ou_periodico: "Revista de \
Psiquiatria" / doi: "10.1590/S0034-72842023000100002"
Saída correta (assumir a incerteza em vez de inventar):
  titulo: (melhor palpite pelo conteúdo/nome do arquivo, sinalizando que é \
incerto se necessário) / autores: [] / editora_ou_periodico: null / \
identificadores: {isbn: null, issn: null, doi: null} / referencia_abnt \
reflete só o que é conhecido, com s.n./s.l./s.d. onde faltar informação.
"""


class ErroDeExtracao(RuntimeError):
    """Falha ao obter metadados válidos do LLM para um documento específico."""


class ErroFatalDeAPI(RuntimeError):
    """Falha que invalida o lote inteiro (Ollama fora do ar, modelo ausente)."""


class ExtratorDeMetadados:
    """Envia o trecho inicial do documento a um Ollama local e valida a resposta."""

    def __init__(self, config: Config, cliente: Optional[httpx.Client] = None) -> None:
        self.config = config
        self.cliente = cliente or httpx.Client(
            base_url=config.ollama_url, timeout=TIMEOUT_PADRAO
        )
        self._esquema = esquema_json_estrito(Metadados)

    def extrair(self, documento: DocumentoConvertido) -> Metadados:
        """Extrai os metadados de um documento já convertido."""
        if not documento.markdown_inicial.strip():
            raise ErroDeExtracao("trecho inicial vazio; nada para analisar")

        corpo = {
            "model": self.config.modelo,
            "messages": [
                {"role": "system", "content": PROMPT_SISTEMA},
                {"role": "user", "content": _montar_prompt(documento)},
            ],
            # `format` com um JSON Schema faz o Ollama restringir a geração à
            # gramática do esquema — equivalente à saída estruturada da API.
            "format": self._esquema,
            "stream": False,
            "options": {"temperature": 0},
        }

        resposta = self._chamar(corpo)

        texto = resposta.get("message", {}).get("content", "")
        if not texto.strip():
            raise ErroDeExtracao("o modelo local não devolveu conteúdo")

        try:
            metadados = Metadados.model_validate(json.loads(texto))
        except json.JSONDecodeError as exc:
            raise ErroDeExtracao(f"resposta do modelo local não é JSON válido: {exc}") from exc
        except ValidationError as exc:
            raise ErroDeExtracao(f"metadados fora do esquema esperado: {exc}") from exc

        metadados = normalizar_maiusculas_na_referencia(metadados)
        return descartar_identificadores_nao_confirmados(
            metadados, documento.markdown_inicial
        )

    def _chamar(self, corpo: dict[str, Any]) -> dict[str, Any]:
        try:
            resposta = self.cliente.post("/api/chat", json=corpo)
            resposta.raise_for_status()
        except httpx.ConnectError as exc:
            raise ErroFatalDeAPI(
                f"não foi possível conectar ao Ollama em {self.config.ollama_url}. "
                "Verifique se ele está rodando (`ollama serve` ou o app Ollama "
                "aberto) — veja o README para instalar."
            ) from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ErroFatalDeAPI(
                    f"modelo '{self.config.modelo}' não encontrado no Ollama. "
                    f"Baixe com: ollama pull {self.config.modelo}"
                ) from exc
            raise ErroDeExtracao(
                f"erro do Ollama ({exc.response.status_code}): {exc.response.text}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise ErroDeExtracao(
                f"tempo esgotado aguardando o Ollama ({TIMEOUT_PADRAO:.0f}s)"
            ) from exc
        except httpx.HTTPError as exc:
            raise ErroDeExtracao(f"falha de conexão com o Ollama: {exc}") from exc

        return resposta.json()


def normalizar_maiusculas_na_referencia(metadados: Metadados) -> Metadados:
    """Força o(s) sobrenome(s) para maiúsculas dentro de `referencia_abnt`.

    A NBR 6023 exige SOBRENOME em maiúsculas apenas na referência formatada —
    em `autores`/`autor_principal` o nome fica em capitalização normal (ex.:
    "Frankl, Viktor E."). Modelos menores nem sempre acertam essa
    capitalização sozinhos; em vez de depender do LLM, comparamos cada nome
    já validado em `autores` com o texto de `referencia_abnt` e forçamos o
    sobrenome para maiúsculas onde encontrarmos uma correspondência literal.
    Não depende do modelo ter acertado a formatação — só de ter citado o
    nome por extenso na referência, que é a própria convenção ABNT.
    """
    referencia = metadados.referencia_abnt
    for autor in metadados.autores:
        if "," not in autor:
            continue
        sobrenome, resto = autor.split(",", 1)
        sobrenome, resto = sobrenome.strip(), resto.strip()
        if not sobrenome or not resto:
            continue
        original = f"{sobrenome}, {resto}"
        maiusculo = f"{sobrenome.upper()}, {resto}"
        if maiusculo not in referencia:
            referencia = referencia.replace(original, maiusculo)

    if referencia == metadados.referencia_abnt:
        return metadados
    return metadados.model_copy(update={"referencia_abnt": referencia})


#: Rótulo usado para cada identificador ao removê-lo também do texto livre
#: de `referencia_abnt` (ex.: "... ISBN 978-85-241-0001-2." → removido).
_ROTULO_DO_IDENTIFICADOR = {"isbn": "ISBN", "issn": "ISSN", "doi": "DOI"}

#: DOI escrito no texto livre da referência, com ou sem rótulo/URL na frente
#: (ex.: "DOI: 10.1000/x", "https://doi.org/10.1000/x", ou só "10.1000/x").
#: O padrão "10.XXXX/algo" é distintivo o bastante para não gerar falso
#: positivo em texto comum — ao contrário de um ISBN, que é só uma sequência
#: de dígitos e poderia colidir com números de página/volume.
_PADRAO_DOI = re.compile(r"10\.\d{4,9}/[^\s,;\"'）)]+")


def _extrair_doi_da_referencia(referencia: str) -> Optional[str]:
    """Acha um DOI escrito dentro do texto livre, mesmo sem rótulo "DOI:"."""
    encontrado = _PADRAO_DOI.search(referencia)
    if not encontrado:
        return None
    return encontrado.group(0).rstrip(".")


def descartar_identificadores_nao_confirmados(
    metadados: Metadados, texto_fonte: str
) -> Metadados:
    """Remove ISBN/ISSN/DOI que não aparecem literalmente no texto analisado.

    Diferente de título/área/subárea, esses três campos são códigos exatos,
    não paráfrases: um ISBN, ISSN ou DOI só é legítimo se estiver de fato no
    trecho que foi enviado ao modelo. Um modelo que reconhece o assunto ou o
    autor por conhecimento próprio (treinamento) pode completar um
    identificador plausível, mas fictício, para o documento errado — essa
    checagem descarta esse caso sem depender do modelo se autocorrigir.
    Não afeta `ano`/`editora_ou_periodico`/demais campos, que são mais
    difíceis de verificar por correspondência exata de texto.

    Quando um identificador é descartado, também remove sua menção pontual
    dentro de `referencia_abnt` (ex.: "ISBN 978-85-241-0001-2.", "DOI:
    10.xxx", ou uma URL "https://doi.org/10.xxx" sem rótulo nenhum) — senão o
    número fabricado continuaria visível na referência formatada, que é o
    texto que alguém de fato copiaria para citar a obra. Um DOI escrito só no
    texto livre (sem preencher `identificadores.doi`) é detectado e tratado
    do mesmo jeito — promovido ao campo estruturado se confirmado, removido
    se não.
    """
    ids = metadados.identificadores
    referencia = metadados.referencia_abnt

    # O modelo às vezes escreve o DOI só na referência em texto livre (ex.:
    # como parte de uma URL), sem preencher o campo estruturado.
    doi_bruto = ids.doi or _extrair_doi_da_referencia(referencia)

    def confirmado(campo: str, valor: Optional[str]) -> Optional[str]:
        nonlocal referencia
        if not valor:
            return None
        if _aparece_no_texto(valor, texto_fonte):
            return valor
        rotulo = _ROTULO_DO_IDENTIFICADOR[campo]
        valor_escapado = re.escape(valor)
        # \b nas duas pontas do valor é essencial: sem isso, um identificador
        # fabricado que por coincidência é uma *substring* de outro código
        # legítimo (ex.: um ISSN "0034-7284" que aparece embutido no meio de
        # um DOI real "10.1590/S0034-72842023...") corrompe o código
        # legítimo em vez de simplesmente não encontrar nada para remover.
        candidatos = (
            rf"\s*\b{rotulo}\b:?\s*\b{valor_escapado}\b\.?",  # "ISBN 978-..." / "DOI: 10.xxx"
            rf"\s*\bhttps?://(?:dx\.|www\.)?doi\.org/{valor_escapado}\b\.?",  # URL de DOI
            rf"\s*\b{valor_escapado}\b\.?",  # valor solto, sem rótulo
        )
        for padrao in candidatos:
            nova_referencia = re.sub(padrao, "", referencia, count=1, flags=re.IGNORECASE)
            if nova_referencia != referencia:
                referencia = nova_referencia
                break
        return None

    novos = Identificadores(
        isbn=confirmado("isbn", ids.isbn),
        issn=confirmado("issn", ids.issn),
        doi=confirmado("doi", doi_bruto),
    )

    atualizacoes: dict[str, Any] = {}
    if novos != ids:
        atualizacoes["identificadores"] = novos
    if referencia != metadados.referencia_abnt:
        atualizacoes["referencia_abnt"] = re.sub(r"\s{2,}", " ", referencia).strip()
    if not atualizacoes:
        return metadados
    return metadados.model_copy(update=atualizacoes)


def _normalizar_para_comparar(texto: str) -> str:
    """Remove espaços, hífens e caixa — para comparar códigos que podem estar
    formatados com espaçamento/traços levemente diferentes no texto."""
    return re.sub(r"[\s\-–—]", "", texto.lower())


def _aparece_no_texto(valor: str, texto: str) -> bool:
    """Confere se `valor` aparece como código isolado em `texto`.

    Tolera espaçamento/traços diferentes entre os caracteres (ex.: ISBN
    escrito como "978 85 323 1005 7" no PDF), mas exige fronteira de palavra
    nas duas pontas — sem isso, um identificador curto fabricado (ex.: um
    ISSN "0034-7284") poderia "bater" só por ser uma substring embutida no
    meio de um código maior e não relacionado (ex.: um DOI real
    "10.1590/S0034-72842023...", convenção comum do SciELO), quando na
    verdade o ISSN em si nunca aparece como código isolado no texto.
    """
    nucleo = re.sub(r"[\s\-–—]", "", valor)
    if not nucleo:
        return False
    padrao = r"[\s\-–—]*".join(re.escape(c) for c in nucleo)
    return re.search(rf"\b{padrao}\b", texto, flags=re.IGNORECASE) is not None


def _montar_prompt(documento: DocumentoConvertido) -> str:
    partes = [
        "Extraia os metadados bibliográficos do documento abaixo.",
        "",
        "## Pistas do arquivo",
        f"- Nome do arquivo: {documento.caminho.name}",
        f"- Total de páginas: {documento.total_paginas}",
    ]
    for chave, valor in documento.metadados_embutidos.items():
        partes.append(f"- Metadado embutido ({chave}): {valor}")

    partes += [
        "",
        "## Trecho inicial do documento",
        "",
        documento.markdown_inicial,
    ]
    return "\n".join(partes)


def esquema_json_estrito(modelo: type) -> dict[str, Any]:
    """Gera o JSON Schema do modelo, endurecido e sem `$ref`/`$defs`.

    "Endurecido": `additionalProperties: false` e `required` com todas as
    propriedades em cada objeto — campos opcionais continuam aceitando
    `null` porque o Pydantic os declara como `anyOf: [tipo, null]`.

    "Sem `$ref`/`$defs`": o parâmetro `format` do Ollama converte o esquema
    em uma gramática (llama.cpp) que não segue referências — objetos e enums
    aninhados (`Identificadores`, `TipoPublicacao`) precisam estar embutidos
    no lugar onde são usados, por isso os `$ref` são resolvidos inline.
    """
    esquema = modelo.model_json_schema()
    _endurecer(esquema)
    for definicao in esquema.get("$defs", {}).values():
        _endurecer(definicao)
    return _resolver_referencias(esquema, esquema.get("$defs", {}))


def _endurecer(no: dict[str, Any]) -> None:
    # `default` e `title` são ruído e podem conflitar com o modo estrito.
    no.pop("default", None)
    no.pop("title", None)

    if no.get("type") == "object" or "properties" in no:
        propriedades = no.get("properties", {})
        no["additionalProperties"] = False
        no["required"] = list(propriedades)
        for subno in propriedades.values():
            if isinstance(subno, dict):
                _endurecer(subno)

    if isinstance(no.get("items"), dict):
        _endurecer(no["items"])

    for chave in ("anyOf", "allOf", "oneOf"):
        for subno in no.get(chave, []):
            if isinstance(subno, dict):
                _endurecer(subno)


def _resolver_referencias(no: Any, definicoes: dict[str, Any]) -> Any:
    if isinstance(no, dict):
        if "$ref" in no:
            nome = no["$ref"].rsplit("/", 1)[-1]
            return _resolver_referencias(copy.deepcopy(definicoes[nome]), definicoes)
        return {
            chave: _resolver_referencias(valor, definicoes)
            for chave, valor in no.items()
            if chave != "$defs"
        }
    if isinstance(no, list):
        return [_resolver_referencias(item, definicoes) for item in no]
    return no
