"""Sanitização de nomes, criação de diretórios e gravação dos arquivos."""

from __future__ import annotations

import re
import shutil
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import yaml

from .models import Metadados

#: Caracteres proibidos em nomes de arquivo (união das regras de Windows/macOS/Linux).
CARACTERES_INVALIDOS = r'[\\/:*?"<>|]'

#: Nomes reservados pelo Windows, independentemente da extensão.
NOMES_RESERVADOS_WINDOWS = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

#: Limite conservador por segmento de caminho (a maioria dos sistemas para em 255).
MAX_CARACTERES_SEGMENTO = 120

#: Limite do nome de arquivo completo, deixando folga para sufixos e extensão.
MAX_CARACTERES_NOME_ARQUIVO = 180

SEM_VALOR = "Sem informação"


class ErroDeOrganizacao(RuntimeError):
    """Falha ao gravar os arquivos no destino."""


@dataclass
class ResultadoDaOrganizacao:
    """Caminhos planejados (dry-run) ou efetivamente gravados."""

    diretorio: Path
    pdf_destino: Path
    markdown_destino: Path
    simulado: bool


def sanitizar(
    texto: Optional[str],
    *,
    max_caracteres: int = MAX_CARACTERES_SEGMENTO,
    finalizar: bool = True,
) -> str:
    """Converte um texto livre em um segmento de caminho seguro.

    Remove caracteres inválidos e de controle, normaliza espaços e trata os
    nomes reservados do Windows. Acentos são preservados (todos os sistemas de
    arquivos alvo aceitam UTF-8), apenas normalizados para a forma NFC.

    `finalizar=False` pula a remoção de ponto/espaço final: use para um
    segmento que ainda será concatenado a outros (como em
    `montar_nome_arquivo`), para não cortar abreviações no meio do nome
    (ex.: "Frankl, Viktor E." viraria "Frankl, Viktor E" mesmo quando não é o
    último pedaço do arquivo). Quem concatena aplica a regra do Windows uma
    única vez, no final da string completa.
    """
    if not texto:
        return ""

    limpo = unicodedata.normalize("NFC", str(texto))
    limpo = re.sub(CARACTERES_INVALIDOS, " ", limpo)
    # Controles viram espaço (e não são apagados) para não colar palavras
    # separadas por quebra de linha ou tabulação.
    limpo = "".join(" " if unicodedata.category(c)[0] == "C" else c for c in limpo)
    limpo = re.sub(r"\s+", " ", limpo).strip()
    if finalizar:
        # Windows rejeita nomes terminados em ponto ou espaço.
        limpo = limpo.rstrip(" .")

    if len(limpo) > max_caracteres:
        limpo = limpo[:max_caracteres].rstrip(" .,;:-")

    if limpo.upper() in NOMES_RESERVADOS_WINDOWS:
        limpo = f"_{limpo}"

    return limpo


def montar_nome_arquivo(metadados: Metadados) -> str:
    """Monta `<TIPO> - <TITULO> - <SUBTITULO> - <AUTOR> - <ANO> - <EDITORA>`.

    Segmentos sem informação são omitidos, evitando separadores órfãos.
    """
    segmentos = [
        metadados.tipo_publicacao.value,
        metadados.titulo,
        metadados.subtitulo,
        metadados.autor_para_nome,
        str(metadados.ano) if metadados.ano else None,
        metadados.editora_ou_periodico,
    ]
    # finalizar=False: a regra de "sem ponto/espaço final" do Windows só se
    # aplica ao fim do nome de arquivo completo, não a cada segmento isolado
    # — senão abreviações como "Frankl, Viktor E." perderiam o ponto mesmo
    # quando seguidas por mais segmentos (ano, editora).
    partes = [sanitizar(segmento, finalizar=False) for segmento in segmentos]
    nome = " - ".join(parte for parte in partes if parte)
    nome = nome.rstrip(" .")

    if not nome:
        nome = SEM_VALOR
    if len(nome) > MAX_CARACTERES_NOME_ARQUIVO:
        nome = nome[:MAX_CARACTERES_NOME_ARQUIVO].rstrip(" -.,;:")
    return nome


#: Subpasta onde entram os arquivos que saíram da extração com algum aviso
#: (divergência de nome de arquivo, identificador não confirmado pela
#: verificação online, ou fallback pago que também ficou incerto) — em vez
#: de ficarem espalhados na árvore normal só marcados por um "!" na tabela do
#: terminal, ficam fisicamente separados para facilitar a revisão manual.
PASTA_REVISAO_MANUAL = "revisao_manual"


def montar_diretorio(
    destino: Path,
    metadados: Metadados,
    *,
    subpasta_markdown: Optional[str] = None,
    revisao_manual: bool = False,
) -> tuple[Path, Path]:
    """Devolve (diretório do PDF, diretório do Markdown).

    A estrutura é `<DESTINO>/<AREA>/<SUBAREA>/<PLURAL_DO_TIPO>/`, ou
    `<DESTINO>/revisao_manual/<AREA>/<SUBAREA>/<PLURAL_DO_TIPO>/` quando
    `revisao_manual=True` — mesma categorização, só isolada num ponto único
    para não se perder entre os arquivos sem aviso. Quando `subpasta_markdown`
    é informado, o `.md` vai para uma subpasta espelho.
    """
    raiz = destino / PASTA_REVISAO_MANUAL if revisao_manual else destino
    area = sanitizar(metadados.area_principal) or SEM_VALOR
    subarea = sanitizar(metadados.subarea) or area
    tipo = sanitizar(metadados.plural_do_tipo) or "Outros"

    diretorio_pdf = raiz / area / subarea / tipo
    diretorio_md = diretorio_pdf
    if subpasta_markdown:
        diretorio_md = diretorio_pdf / (sanitizar(subpasta_markdown) or "Markdown")
    return diretorio_pdf, diretorio_md


def caminho_disponivel(caminho: Path) -> Path:
    """Acrescenta ` (2)`, ` (3)`… se o caminho já existir."""
    if not caminho.exists():
        return caminho
    contador = 2
    while True:
        candidato = caminho.with_name(f"{caminho.stem} ({contador}){caminho.suffix}")
        if not candidato.exists():
            return candidato
        contador += 1


def gerar_markdown(
    metadados: Metadados,
    conteudo: str,
    *,
    arquivo_origem: Optional[Path] = None,
    total_paginas: Optional[int] = None,
) -> str:
    """Monta o `.md` com YAML frontmatter compatível com o Obsidian."""
    frontmatter: dict[str, object] = {
        "tipo_publicacao": metadados.tipo_publicacao.value,
        "area_principal": metadados.area_principal,
        "subarea": metadados.subarea,
        "titulo": metadados.titulo,
        "subtitulo": metadados.subtitulo,
        "autores": metadados.autores,
        "autor_principal": metadados.autor_para_nome,
        "editora_ou_periodico": metadados.editora_ou_periodico,
        "ano": metadados.ano,
        "local": metadados.local,
        "isbn": metadados.identificadores.isbn,
        "issn": metadados.identificadores.issn,
        "doi": metadados.identificadores.doi,
        "referencia_abnt": metadados.referencia_abnt,
        # Tags no formato hierárquico do Obsidian.
        "tags": [
            f"area/{_slug(metadados.area_principal)}",
            f"subarea/{_slug(metadados.subarea)}",
            f"tipo/{_slug(metadados.tipo_publicacao.value)}",
        ],
        "arquivo_origem": arquivo_origem.name if arquivo_origem else None,
        "total_paginas": total_paginas,
        "catalogado_em": date.today().isoformat(),
    }

    yaml_texto = yaml.safe_dump(
        frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False
    ).rstrip()

    referencia = metadados.referencia_abnt.strip() or SEM_VALOR

    return (
        f"---\n{yaml_texto}\n---\n\n"
        f"# {metadados.titulo}\n\n"
        "## Referência Bibliográfica (ABNT)\n\n"
        f"> {referencia}\n\n"
        "---\n\n"
        "## Conteúdo\n\n"
        f"{conteudo.strip()}\n"
    )


def organizar(
    metadados: Metadados,
    *,
    pdf_origem: Path,
    destino: Path,
    markdown: str,
    subpasta_markdown: Optional[str] = None,
    mover: bool = False,
    dry_run: bool = False,
    revisao_manual: bool = False,
) -> ResultadoDaOrganizacao:
    """Grava o PDF renomeado e o Markdown no destino (ou apenas planeja)."""
    diretorio_pdf, diretorio_md = montar_diretorio(
        destino,
        metadados,
        subpasta_markdown=subpasta_markdown,
        revisao_manual=revisao_manual,
    )
    nome = montar_nome_arquivo(metadados)
    destino_pdf = diretorio_pdf / f"{nome}.pdf"
    destino_md = diretorio_md / f"{nome}.md"

    if dry_run:
        return ResultadoDaOrganizacao(
            diretorio=diretorio_pdf,
            pdf_destino=destino_pdf,
            markdown_destino=destino_md,
            simulado=True,
        )

    try:
        diretorio_pdf.mkdir(parents=True, exist_ok=True)
        diretorio_md.mkdir(parents=True, exist_ok=True)

        destino_pdf = caminho_disponivel(destino_pdf)
        # Mantém o par PDF/Markdown com o mesmo nome quando houve desambiguação.
        destino_md = diretorio_md / f"{destino_pdf.stem}.md"

        if mover:
            shutil.move(str(pdf_origem), destino_pdf)
        else:
            shutil.copy2(pdf_origem, destino_pdf)

        destino_md.write_text(markdown, encoding="utf-8")
    except OSError as exc:
        raise ErroDeOrganizacao(f"falha ao gravar em {diretorio_pdf}: {exc}") from exc

    return ResultadoDaOrganizacao(
        diretorio=diretorio_pdf,
        pdf_destino=destino_pdf,
        markdown_destino=destino_md,
        simulado=False,
    )


def _slug(texto: str) -> str:
    """Converte um rótulo em uma tag do Obsidian (sem espaços nem acentos)."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", sem_acento.lower()).strip("-") or "indefinido"
