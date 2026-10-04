"""Tests for Markdown conversion, YAML Frontmatter assembly, and filename standardization."""

import yaml
from organizador_pdf.converter import MarkdownConverter, generate_standardized_filename, sanitize_filename
from organizador_pdf.models import PublicationMetadata, Identifiers


def test_sanitize_filename():
    assert sanitize_filename("Título: Subtítulo / Algo? *Teste*") == "Título - Subtítulo - Algo - Teste"
    assert sanitize_filename("A/B\\C:D*E?F\"G<H>I|J") == "A - B - C - D - E - F - G - H - I - J"


def test_generate_standardized_filename():
    # 1. Single author with year
    meta1 = PublicationMetadata(
        title="Negritude sem identidade",
        authors=["Érico Andrade"],
        year=2023,
        classification="livro",
    )
    assert generate_standardized_filename(meta1) == "ANDRADE, Érico - Negritude sem identidade - (2023)"

    # 2. Multiple authors with subtitle and year
    meta2 = PublicationMetadata(
        title="Avanços em IA",
        subtitle="Raciocínio Estruturado",
        authors=["Carlos Silva", "Ana Souza"],
        year=2025,
        classification="artigo",
    )
    assert generate_standardized_filename(meta2) == "SILVA, Carlos et al. - Avanços em IA - Raciocínio Estruturado - (2025)"

    # 3. Without author
    meta3 = PublicationMetadata(
        title="Psicologia Social",
        subtitle="Livro Didático",
        year=2011,
        classification="apostila",
    )
    assert generate_standardized_filename(meta3) == "Psicologia Social - Livro Didático - (2011)"

    # 4. Without year
    meta4 = PublicationMetadata(
        title="O Banquete",
        authors=["Platão"],
        classification="livro",
    )
    assert generate_standardized_filename(meta4) == "PLATÃO - O Banquete"


def test_converter_yaml_frontmatter():
    meta = PublicationMetadata(
        title="Refactoring: Improving the Design of Existing Code",
        authors=["Martin Fowler", "Kent Beck"],
        publisher="Addison-Wesley",
        edition="2. ed.",
        year=2018,
        classification="livro",
        identifiers=Identifiers(isbn="9780134757599", doi="", issn=""),
    )

    frontmatter = MarkdownConverter.build_yaml_frontmatter(meta)
    assert frontmatter.startswith("---\n")
    assert frontmatter.endswith("\n---\n\n")

    parsed = yaml.safe_load(frontmatter.strip().strip("-").strip())
    assert parsed["title"] == "Refactoring: Improving the Design of Existing Code"
    assert parsed["authors"] == ["Martin Fowler", "Kent Beck"]
    assert parsed["publisher"] == "Addison-Wesley"
    assert parsed["edition"] == "2. ed."
    assert parsed["identifiers"]["isbn"] == "9780134757599"
    assert parsed["classification"] == "livro"


def test_converter_assemble_markdown():
    meta = PublicationMetadata(
        title="Estudo sobre Redes Neurais",
        authors=["Alan Turing"],
        publisher="Oxford Press",
        classification="artigo",
    )
    abnt_ref = "TURING, Alan. **Estudo sobre Redes Neurais**. Oxford Press, 2024."

    md_output = MarkdownConverter.assemble_markdown(meta, abnt_ref)

    assert md_output.startswith("---")
    assert "# Estudo sobre Redes Neurais" in md_output
    assert "## Referência Bibliográfica" in md_output
    assert abnt_ref in md_output
