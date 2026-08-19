from __future__ import annotations

from pathlib import Path

import yaml

import pytest

from organizador_pdf.models import Metadados, TipoPublicacao
from organizador_pdf.organizer import (
    ErroDeOrganizacao,
    MAX_CARACTERES_CAMINHO,
    MAX_CARACTERES_NOME_ARQUIVO,
    MIN_CARACTERES_NOME_TRUNCADO,
    _truncar_para_caminho_seguro,
    caminho_disponivel,
    gerar_markdown,
    montar_diretorio,
    montar_nome_arquivo,
    organizar,
    sanitizar,
)


class TestSanitizar:
    def test_remove_caracteres_invalidos(self):
        assert sanitizar(r'Psi/co\lo:gia*?"<>|') == "Psi co lo gia"

    def test_preserva_acentos(self):
        assert sanitizar("Dissertação sobre Ética") == "Dissertação sobre Ética"

    def test_colapsa_espacos_e_remove_controle(self):
        assert sanitizar("Título\n\tcom   quebras") == "Título com quebras"

    def test_remove_ponto_e_espaco_finais(self):
        # Windows rejeita nomes terminados em '.' ou ' '.
        assert sanitizar("Nome do arquivo. ") == "Nome do arquivo"

    def test_prefixa_nomes_reservados_do_windows(self):
        assert sanitizar("CON") == "_CON"
        assert sanitizar("lpt1") == "_lpt1"

    def test_trunca_no_limite(self):
        assert len(sanitizar("A" * 500)) == 120

    def test_valor_vazio_ou_nulo(self):
        assert sanitizar(None) == ""
        assert sanitizar("   ") == ""


class TestMontarNomeArquivo:
    def test_padrao_completo(self, metadados: Metadados):
        assert montar_nome_arquivo(metadados) == (
            "Livro - Em Busca de Sentido - Um psicólogo no campo de concentração"
            " - Frankl, Viktor E. - 2019 - Vozes"
        )

    def test_omite_segmentos_ausentes(self, metadados: Metadados):
        metadados.subtitulo = None
        metadados.ano = None
        metadados.editora_ou_periodico = None
        # Sem separadores órfãos onde faltou informação. O ponto final de
        # "Frankl, Viktor E." some aqui porque agora é o último segmento do
        # nome completo (regra do Windows: sem ponto/espaço final).
        assert montar_nome_arquivo(metadados) == (
            "Livro - Em Busca de Sentido - Frankl, Viktor E"
        )

    def test_abreviacao_no_meio_do_nome_preserva_o_ponto(self, metadados: Metadados):
        # Quando "Frankl, Viktor E." NÃO é o último segmento (há ano/editora
        # depois), o ponto da abreviação deve ser preservado — só o fim do
        # nome de arquivo completo precisa respeitar a regra do Windows.
        nome = montar_nome_arquivo(metadados)
        assert "Frankl, Viktor E. - 2019" in nome

    def test_usa_primeiro_autor_quando_principal_ausente(self, metadados: Metadados):
        metadados.autor_principal = None
        metadados.autores = ["Carl Rogers", "Outro Autor"]
        assert "Carl Rogers" in montar_nome_arquivo(metadados)

    def test_respeita_limite_de_tamanho(self, metadados: Metadados):
        metadados.titulo = "T" * 300
        assert len(montar_nome_arquivo(metadados)) <= MAX_CARACTERES_NOME_ARQUIVO


class TestMontarDiretorio:
    def test_estrutura_hierarquica(self, metadados: Metadados, tmp_path: Path):
        pdf, md = montar_diretorio(tmp_path, metadados)
        assert pdf == tmp_path / "Psicologia" / "Logoterapia" / "Livros"
        assert md == pdf

    def test_pluraliza_cada_tipo(self, metadados: Metadados, tmp_path: Path):
        esperados = {
            TipoPublicacao.LIVRO: "Livros",
            TipoPublicacao.ARTIGO: "Artigos",
            TipoPublicacao.DISSERTACAO_TESE: "Dissertações e Teses",
            TipoPublicacao.APOSTILA: "Apostilas",
            TipoPublicacao.REVISTA: "Revistas",
            TipoPublicacao.CAPITULO_LIVRO: "Capítulos de Livro",
            TipoPublicacao.OUTROS: "Outros",
        }
        for tipo, pasta in esperados.items():
            metadados.tipo_publicacao = tipo
            pdf, _ = montar_diretorio(tmp_path, metadados)
            assert pdf.name == pasta

    def test_subpasta_espelho_para_markdown(self, metadados: Metadados, tmp_path: Path):
        pdf, md = montar_diretorio(tmp_path, metadados, subpasta_markdown="Markdown")
        assert md == pdf / "Markdown"

    def test_subarea_vazia_cai_para_a_area(self, metadados: Metadados, tmp_path: Path):
        metadados.subarea = "   "
        pdf, _ = montar_diretorio(tmp_path, metadados)
        assert pdf == tmp_path / "Psicologia" / "Psicologia" / "Livros"

    def test_revisao_manual_aninha_a_mesma_estrutura_numa_subpasta(
        self, metadados: Metadados, tmp_path: Path
    ):
        pdf, md = montar_diretorio(tmp_path, metadados, revisao_manual=True)
        assert pdf == tmp_path / "revisao_manual" / "Psicologia" / "Logoterapia" / "Livros"
        assert md == pdf

    def test_sem_revisao_manual_nao_aninha(self, metadados: Metadados, tmp_path: Path):
        pdf, _ = montar_diretorio(tmp_path, metadados, revisao_manual=False)
        assert "revisao_manual" not in pdf.parts


class TestGerarMarkdown:
    def test_frontmatter_valido_e_secoes(self, metadados: Metadados):
        conteudo = gerar_markdown(metadados, "Texto do PDF.", total_paginas=10)

        assert conteudo.startswith("---\n")
        bruto = conteudo.split("---", 2)[1]
        dados = yaml.safe_load(bruto)

        assert dados["titulo"] == "Em Busca de Sentido"
        assert dados["autores"] == ["Frankl, Viktor E."]
        assert dados["ano"] == 2019
        assert dados["isbn"] == "978-85-326-0871-3"
        assert dados["total_paginas"] == 10
        assert "area/psicologia" in dados["tags"]

        assert "## Referência Bibliográfica (ABNT)" in conteudo
        assert f"> {metadados.referencia_abnt}" in conteudo
        assert "## Conteúdo" in conteudo
        assert conteudo.rstrip().endswith("Texto do PDF.")

    def test_acentos_nao_sao_escapados(self, metadados: Metadados):
        conteudo = gerar_markdown(metadados, "corpo")
        assert "Psicologia" in conteudo
        assert "\\u" not in conteudo

    def test_registra_provedor_de_extracao(self, metadados: Metadados):
        conteudo = gerar_markdown(
            metadados, "corpo", provedor_extracao="ollama", extraido_via_fallback=False
        )
        dados = yaml.safe_load(conteudo.split("---", 2)[1])
        assert dados["provedor_extracao"] == "ollama"
        assert dados["extraido_via_fallback"] is False

    def test_registra_quando_veio_do_fallback(self, metadados: Metadados):
        conteudo = gerar_markdown(
            metadados, "corpo", provedor_extracao="anthropic", extraido_via_fallback=True
        )
        dados = yaml.safe_load(conteudo.split("---", 2)[1])
        assert dados["provedor_extracao"] == "anthropic"
        assert dados["extraido_via_fallback"] is True

    def test_padrao_sem_provedor_informado(self, metadados: Metadados):
        conteudo = gerar_markdown(metadados, "corpo")
        dados = yaml.safe_load(conteudo.split("---", 2)[1])
        assert dados["provedor_extracao"] is None
        assert dados["extraido_via_fallback"] is False


class TestCaminhoDisponivel:
    def test_devolve_o_mesmo_quando_livre(self, tmp_path: Path):
        alvo = tmp_path / "a.pdf"
        assert caminho_disponivel(alvo) == alvo

    def test_incrementa_sufixo(self, tmp_path: Path):
        (tmp_path / "a.pdf").touch()
        assert caminho_disponivel(tmp_path / "a.pdf").name == "a (2).pdf"

        (tmp_path / "a (2).pdf").touch()
        assert caminho_disponivel(tmp_path / "a.pdf").name == "a (3).pdf"


class TestOrganizar:
    def test_dry_run_nao_grava_nada(self, metadados: Metadados, tmp_path: Path):
        origem = tmp_path / "entrada.pdf"
        origem.write_bytes(b"%PDF-1.4 fake")
        destino = tmp_path / "saida"

        resultado = organizar(
            metadados,
            pdf_origem=origem,
            destino=destino,
            markdown="# md",
            dry_run=True,
        )

        assert resultado.simulado is True
        assert not destino.exists()
        assert resultado.pdf_destino.suffix == ".pdf"
        assert resultado.markdown_destino.suffix == ".md"

    def test_copia_e_grava_o_par(self, metadados: Metadados, tmp_path: Path):
        origem = tmp_path / "entrada.pdf"
        origem.write_bytes(b"%PDF-1.4 fake")
        destino = tmp_path / "saida"

        resultado = organizar(
            metadados,
            pdf_origem=origem,
            destino=destino,
            markdown="# md",
        )

        assert origem.exists(), "o padrão é copiar, não mover"
        assert resultado.pdf_destino.exists()
        assert resultado.markdown_destino.exists()
        assert resultado.pdf_destino.stem == resultado.markdown_destino.stem
        assert resultado.pdf_destino.parent == (
            destino / "Psicologia" / "Logoterapia" / "Livros"
        )

    def test_revisao_manual_grava_na_subpasta(self, metadados: Metadados, tmp_path: Path):
        origem = tmp_path / "entrada.pdf"
        origem.write_bytes(b"%PDF-1.4 fake")
        destino = tmp_path / "saida"

        resultado = organizar(
            metadados,
            pdf_origem=origem,
            destino=destino,
            markdown="# md",
            revisao_manual=True,
        )

        assert resultado.pdf_destino.exists()
        assert resultado.pdf_destino.parent == (
            destino / "revisao_manual" / "Psicologia" / "Logoterapia" / "Livros"
        )

    def test_mover_remove_a_origem(self, metadados: Metadados, tmp_path: Path):
        origem = tmp_path / "entrada.pdf"
        origem.write_bytes(b"%PDF-1.4 fake")

        organizar(
            metadados,
            pdf_origem=origem,
            destino=tmp_path / "saida",
            markdown="# md",
            mover=True,
        )

        assert not origem.exists()

    def test_colisao_mantem_pdf_e_md_pareados(self, metadados: Metadados, tmp_path: Path):
        destino = tmp_path / "saida"
        for indice in range(2):
            origem = tmp_path / f"entrada{indice}.pdf"
            origem.write_bytes(b"%PDF-1.4 fake")
            resultado = organizar(
                metadados, pdf_origem=origem, destino=destino, markdown="# md"
            )

        assert resultado.pdf_destino.name.endswith("(2).pdf")
        assert resultado.markdown_destino.name.endswith("(2).md")
        assert resultado.markdown_destino.exists()

    def test_destino_profundo_trunca_o_nome_para_caber_no_limite_do_windows(
        self, metadados: Metadados, tmp_path: Path
    ):
        origem = tmp_path / "entrada.pdf"
        origem.write_bytes(b"%PDF-1.4 fake")
        # tmp_path já é um caminho absoluto real; empilha subpastas em cima
        # pra simular um --destino fundo (ex.: OneDrive aninhado no Windows).
        destino = tmp_path.joinpath(*[f"pasta{i}" for i in range(10)])
        metadados.titulo = "T" * 300  # nome, sem o truncamento novo, estouraria

        resultado = organizar(
            metadados, pdf_origem=origem, destino=destino, markdown="# md"
        )

        assert resultado.pdf_destino.exists()
        assert len(str(resultado.pdf_destino)) <= MAX_CARACTERES_CAMINHO
        assert resultado.pdf_destino.stem == resultado.markdown_destino.stem

    def test_destino_absurdamente_profundo_falha_com_mensagem_clara(
        self, metadados: Metadados, tmp_path: Path
    ):
        origem = tmp_path / "entrada.pdf"
        origem.write_bytes(b"%PDF-1.4 fake")
        destino = tmp_path.joinpath(*[f"subpasta-bem-comprida-{i}" for i in range(20)])

        with pytest.raises(ErroDeOrganizacao, match="destino é longo demais"):
            organizar(metadados, pdf_origem=origem, destino=destino, markdown="# md")


class TestTruncarParaCaminhoSeguro:
    """Unidade isolada (sem tocar disco) — a integração com `organizar` é
    coberta acima; aqui o controle sobre o comprimento do diretório é total,
    sem depender de o quão fundo é o `tmp_path` do ambiente de teste."""

    def test_nao_mexe_quando_ja_cabe(self):
        diretorio = Path("/destino/curto")
        assert _truncar_para_caminho_seguro("nome", diretorio, ".pdf") == "nome"

    def test_corta_exatamente_no_orcamento_disponivel(self):
        diretorio = Path("/d" * 100)  # bem mais fundo que o razoável
        nome = "N" * 300
        resultado = _truncar_para_caminho_seguro(nome, diretorio, ".pdf")

        assert len(resultado) < len(nome)
        caminho_final = len(str(diretorio)) + 1 + len(resultado) + len(".pdf")
        assert caminho_final <= MAX_CARACTERES_CAMINHO

    def test_levanta_erro_quando_nem_o_minimo_cabe(self):
        diretorio = Path("/d" * 200)  # não sobra nem o piso mínimo
        with pytest.raises(ErroDeOrganizacao, match="destino é longo demais"):
            _truncar_para_caminho_seguro("N" * 300, diretorio, ".pdf")

    def test_nunca_devolve_abaixo_do_minimo(self):
        # Orçamento levemente acima do piso: deve ou cortar respeitando o
        # piso, ou levantar erro — nunca devolver algo menor que o piso.
        diretorio = Path("/d" * 90)
        try:
            resultado = _truncar_para_caminho_seguro("N" * 300, diretorio, ".pdf")
        except ErroDeOrganizacao:
            return
        assert len(resultado) >= MIN_CARACTERES_NOME_TRUNCADO
