from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from organizador_pdf.config import Config
from organizador_pdf.converter import DocumentoConvertido
from organizador_pdf.extractor import (
    ErroDeExtracao,
    ErroFatalDeAPI,
    ExtratorOllama,
    descartar_identificadores_nao_confirmados,
    esquema_json_estrito,
    normalizar_maiusculas_na_referencia,
)
from organizador_pdf.models import Identificadores, Metadados, TipoPublicacao


def documento(texto: str = "Em Busca de Sentido — Viktor Frankl") -> DocumentoConvertido:
    return DocumentoConvertido(
        caminho=Path("/tmp/livro.pdf"),
        markdown_completo=texto,
        markdown_inicial=texto,
        total_paginas=3,
        metadados_embutidos={"title": "Em Busca de Sentido"},
    )


PAYLOAD_VALIDO = {
    "tipo_publicacao": "Livro",
    "area_principal": "Psicologia",
    "subarea": "Logoterapia",
    "titulo": "Em Busca de Sentido",
    "subtitulo": None,
    "autores": ["Frankl, Viktor E."],
    "autor_principal": "Frankl, Viktor E.",
    "editora_ou_periodico": "Vozes",
    "ano": 2019,
    "local": "Petrópolis",
    "identificadores": {"isbn": None, "issn": None, "doi": None},
    "referencia_abnt": "FRANKL, Viktor E. Em busca de sentido. Petrópolis: Vozes, 2019.",
}


def cliente_com(handler) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://localhost:11434"
    )


def config_local(**extra) -> Config:
    return Config(modelo="qwen2.5:3b-instruct", **extra)


def _metadados_minimos(*, autores, autor_principal, referencia_abnt) -> Metadados:
    return Metadados(
        tipo_publicacao=TipoPublicacao.LIVRO,
        area_principal="Psicologia",
        subarea="Logoterapia",
        titulo="Em Busca de Sentido",
        autores=autores,
        autor_principal=autor_principal,
        identificadores=Identificadores(),
        referencia_abnt=referencia_abnt,
    )


class TestNormalizarMaiusculasNaReferencia:
    def test_corrige_sobrenome_em_minusculas(self):
        # Comportamento típico de um modelo local menor: acerta o formato em
        # `autores`, mas esquece de colocar o sobrenome em maiúsculas dentro
        # da referência formatada.
        metadados = _metadados_minimos(
            autores=["Frankl, Viktor E."],
            autor_principal="Frankl, Viktor E.",
            referencia_abnt="Frankl, Viktor E. Em Busca de Sentido. Vozes, 2019.",
        )

        corrigido = normalizar_maiusculas_na_referencia(metadados)

        assert corrigido.referencia_abnt == (
            "FRANKL, Viktor E. Em Busca de Sentido. Vozes, 2019."
        )
        # Só a referência muda — autores continua em capitalização normal.
        assert corrigido.autores == ["Frankl, Viktor E."]

    def test_multiplos_autores(self):
        metadados = _metadados_minimos(
            autores=["Souza, Ana", "Lima, Bruno"],
            autor_principal="Souza, Ana",
            referencia_abnt="Souza, Ana; Lima, Bruno. Artigo. Revista, 2021.",
        )

        corrigido = normalizar_maiusculas_na_referencia(metadados)

        assert corrigido.referencia_abnt == (
            "SOUZA, Ana; LIMA, Bruno. Artigo. Revista, 2021."
        )

    def test_ja_correto_fica_intacto(self):
        metadados = _metadados_minimos(
            autores=["Frankl, Viktor E."],
            autor_principal="Frankl, Viktor E.",
            referencia_abnt="FRANKL, Viktor E. Em Busca de Sentido. Vozes, 2019.",
        )

        corrigido = normalizar_maiusculas_na_referencia(metadados)

        assert corrigido.referencia_abnt == metadados.referencia_abnt

    def test_nome_nao_citado_na_referencia_nao_quebra(self):
        # Referência que não repete o nome do autor por extenso (obra
        # institucional, por exemplo) — a normalização não deve alterar nada.
        metadados = _metadados_minimos(
            autores=["Frankl, Viktor E."],
            autor_principal="Frankl, Viktor E.",
            referencia_abnt="ORGANIZAÇÃO MUNDIAL DA SAÚDE. Relatório anual. Genebra, 2021.",
        )

        corrigido = normalizar_maiusculas_na_referencia(metadados)

        assert corrigido.referencia_abnt == metadados.referencia_abnt

    def test_autor_sem_virgula_e_ignorado_com_seguranca(self):
        # `autores` fora do formato esperado (ex.: nome institucional sem
        # vírgula) não deve levantar exceção nem corromper a referência.
        metadados = _metadados_minimos(
            autores=["Organização Mundial da Saúde"],
            autor_principal="Organização Mundial da Saúde",
            referencia_abnt="ORGANIZAÇÃO MUNDIAL DA SAÚDE. Relatório anual. Genebra, 2021.",
        )

        corrigido = normalizar_maiusculas_na_referencia(metadados)

        assert corrigido.referencia_abnt == metadados.referencia_abnt


class TestDescartarIdentificadoresNaoConfirmados:
    def test_remove_isbn_fabricado(self):
        # Caso real: o modelo reconheceu o autor certo (por conhecimento
        # próprio) mas completou um ISBN plausível que não está no PDF.
        metadados = _metadados_minimos(
            autores=["Foucault, Michel"],
            autor_principal="Foucault, Michel",
            referencia_abnt="FOUCAULT, Michel. Obra. Ministério da Saúde, 1972.",
        )
        metadados.identificadores = Identificadores(isbn="978-85-241-0001-2")

        corrigido = descartar_identificadores_nao_confirmados(
            metadados, "Texto do PDF sem nenhum ISBN mencionado em lugar nenhum."
        )

        assert corrigido.identificadores.isbn is None

    def test_mantem_identificador_presente_no_texto(self):
        metadados = _metadados_minimos(
            autores=["Frazão, Lilian Meyer"],
            autor_principal="Frazão, Lilian Meyer",
            referencia_abnt="FRAZÃO, Lilian Meyer. Obra. Summus, 2015.",
        )
        metadados.identificadores = Identificadores(isbn="978-85-323-1005-7")

        corrigido = descartar_identificadores_nao_confirmados(
            metadados, "... ISBN 978-85-323-1005-7 (recurso eletrônico) ..."
        )

        assert corrigido.identificadores.isbn == "978-85-323-1005-7"

    def test_tolera_formatacao_diferente_hifen_espaco(self):
        # O texto pode formatar o ISBN com espaços/hífens diferentes do que
        # o modelo devolveu — a comparação ignora isso.
        metadados = _metadados_minimos(
            autores=["Autor, Nome"],
            autor_principal="Autor, Nome",
            referencia_abnt="AUTOR, Nome. Obra.",
        )
        metadados.identificadores = Identificadores(isbn="978-85-323-1005-7")

        corrigido = descartar_identificadores_nao_confirmados(
            metadados, "ISBN: 978 85 323 1005 7 (eletrônico)"
        )

        assert corrigido.identificadores.isbn == "978-85-323-1005-7"

    def test_doi_so_no_texto_livre_sem_campo_estruturado_e_removido(self):
        # Caso real: o modelo escreveu o DOI como parte de uma URL dentro da
        # referência, sem preencher `identificadores.doi` — o campo
        # estruturado fica null, mas o número fabricado continuava visível
        # no texto que o usuário copiaria para citar.
        metadados = _metadados_minimos(
            autores=["Silva, Ana"],
            autor_principal="Silva, Ana",
            referencia_abnt=(
                "Silva, A. (2023). O Internamento. Revista de Psiquiatria, "
                "12(1), 1-20. https://doi.org/10.1590/S0034-72842023000100002"
            ),
        )
        # identificadores.doi já é null por padrão em _metadados_minimos

        corrigido = descartar_identificadores_nao_confirmados(
            metadados, "Texto do PDF sem nenhum DOI mencionado."
        )

        assert corrigido.identificadores.doi is None
        assert "10.1590" not in corrigido.referencia_abnt
        assert "doi.org" not in corrigido.referencia_abnt

    def test_doi_so_no_texto_livre_mas_confirmado_e_promovido(self):
        # Se o DOI que só estava no texto livre realmente aparece no PDF,
        # deve ser promovido para o campo estruturado, não descartado.
        metadados = _metadados_minimos(
            autores=["Souza, Ana"],
            autor_principal="Souza, Ana",
            referencia_abnt="SOUZA, Ana. Artigo. DOI: 10.1000/xyz123.",
        )

        corrigido = descartar_identificadores_nao_confirmados(
            metadados, "... disponível em DOI 10.1000/xyz123 ..."
        )

        assert corrigido.identificadores.doi == "10.1000/xyz123"
        assert "10.1000/xyz123" in corrigido.referencia_abnt  # confirmado, mantido

    def test_string_de_incerteza_e_removida(self):
        # O modelo às vezes escreve "Não informado" em vez de null — como
        # essa string também não aparece no texto-fonte, é removida do
        # mesmo jeito, sem precisar de um caso especial.
        metadados = _metadados_minimos(
            autores=["Autor, Nome"],
            autor_principal="Autor, Nome",
            referencia_abnt="AUTOR, Nome. Obra.",
        )
        metadados.identificadores = Identificadores(
            isbn="Não informado", issn="Não informado", doi="Não informado"
        )

        corrigido = descartar_identificadores_nao_confirmados(metadados, "texto qualquer")

        assert corrigido.identificadores.esta_vazio()

    def test_remove_tambem_da_referencia_abnt(self):
        # Reproduz o caso real: identificadores fabricados aparecem tanto
        # nos campos estruturados quanto embutidos no texto da referência.
        metadados = _metadados_minimos(
            autores=["Foucault, Michel"],
            autor_principal="Foucault, Michel",
            referencia_abnt=(
                "FOUCAULT, M. A experiência contemporânea da loucura. Brasília: "
                "Ministério da Saúde, 1972. 120 p. (Série B, 12). ISBN "
                "978-85-241-0001-2. ISSN 0000-0001. DOI: 10.1000/01."
            ),
        )
        metadados.identificadores = Identificadores(
            isbn="978-85-241-0001-2", issn="0000-0001", doi="10.1000/01"
        )

        corrigido = descartar_identificadores_nao_confirmados(
            metadados, "Texto do PDF sem nenhum identificador mencionado."
        )

        assert corrigido.identificadores.esta_vazio()
        assert "978-85-241-0001-2" not in corrigido.referencia_abnt
        assert "0000-0001" not in corrigido.referencia_abnt
        assert "10.1000/01" not in corrigido.referencia_abnt
        assert corrigido.referencia_abnt.startswith(
            "FOUCAULT, M. A experiência contemporânea da loucura. Brasília: "
            "Ministério da Saúde, 1972. 120 p. (Série B, 12)."
        )

    def test_identificador_confirmado_nao_altera_referencia(self):
        metadados = _metadados_minimos(
            autores=["Frazão, Lilian Meyer"],
            autor_principal="Frazão, Lilian Meyer",
            referencia_abnt="FRAZÃO, Lilian Meyer. Obra. ISBN 978-85-323-1005-7. Summus, 2015.",
        )
        metadados.identificadores = Identificadores(isbn="978-85-323-1005-7")

        corrigido = descartar_identificadores_nao_confirmados(
            metadados, "... ISBN 978-85-323-1005-7 (recurso eletrônico) ..."
        )

        assert corrigido.referencia_abnt == metadados.referencia_abnt

    def test_issn_fabricado_nao_corrompe_doi_confirmado_que_o_contem_como_substring(self):
        # Bug real encontrado em validação ao vivo: o modelo devolveu um ISSN
        # fabricado ("0034-7284") que por coincidência é uma substring dentro
        # dos dígitos de um DOI legítimo e confirmado
        # ("10.1590/S0034-72842023000100002"). Sem âncoras de limite de
        # palavra (\b), a remoção do ISSN não confirmado corrompia o DOI
        # confirmado ao invés de deixá-lo intacto.
        doi_real = "10.1590/S0034-72842023000100002"
        metadados = _metadados_minimos(
            autores=["Silva, Ana"],
            autor_principal="Silva, Ana",
            referencia_abnt=(
                f"SILVA, A. O Internamento. Revista de Psiquiatria, 12(1), 1-20. "
                f"DOI: {doi_real}."
            ),
        )
        metadados.identificadores = Identificadores(
            isbn=None, issn="0034-7284", doi=doi_real
        )

        corrigido = descartar_identificadores_nao_confirmados(
            metadados, f"Texto do PDF que menciona apenas o DOI {doi_real}, sem ISSN."
        )

        assert corrigido.identificadores.issn is None
        assert corrigido.identificadores.doi == doi_real
        assert doi_real in corrigido.referencia_abnt

    def test_sem_identificadores_nao_faz_nada(self):
        metadados = _metadados_minimos(
            autores=["Autor, Nome"],
            autor_principal="Autor, Nome",
            referencia_abnt="AUTOR, Nome. Obra.",
        )
        corrigido = descartar_identificadores_nao_confirmados(metadados, "texto qualquer")
        assert corrigido is metadados  # nada mudou — mesmo objeto de volta


class TestEsquemaJsonEstrito:
    def test_objetos_sao_estritos(self):
        esquema = esquema_json_estrito(Metadados)

        assert esquema["additionalProperties"] is False
        assert set(esquema["required"]) == set(esquema["properties"])

        identificadores = esquema["properties"]["identificadores"]
        assert identificadores["additionalProperties"] is False
        assert set(identificadores["required"]) == {"isbn", "issn", "doi"}

    def test_sem_chaves_default(self):
        esquema = esquema_json_estrito(Metadados)
        assert "default" not in json.dumps(esquema)

    def test_enum_de_tipos_preservado(self):
        esquema = esquema_json_estrito(Metadados)
        tipo = esquema["properties"]["tipo_publicacao"]
        assert set(tipo["enum"]) == {t.value for t in TipoPublicacao}

    def test_nao_tem_ref_nem_defs(self):
        # O `format` do Ollama converte o esquema em gramática (llama.cpp) e
        # não segue $ref — os objetos/enums aninhados precisam vir embutidos.
        esquema = esquema_json_estrito(Metadados)
        assert "$defs" not in esquema
        assert "$ref" not in json.dumps(esquema)

    def test_objeto_aninhado_foi_embutido(self):
        esquema = esquema_json_estrito(Metadados)
        identificadores = esquema["properties"]["identificadores"]
        assert "properties" in identificadores
        assert set(identificadores["properties"]) == {"isbn", "issn", "doi"}


class TestExtrair:
    def test_resposta_valida_vira_metadados(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/chat"
            corpo = json.loads(request.content)
            assert corpo["model"] == "qwen2.5:3b-instruct"
            assert corpo["stream"] is False
            return httpx.Response(
                200, json={"message": {"content": json.dumps(PAYLOAD_VALIDO)}}
            )

        extrator = ExtratorOllama(config_local(), cliente=cliente_com(handler))
        metadados = extrator.extrair(documento())

        assert metadados.titulo == "Em Busca de Sentido"
        assert metadados.tipo_publicacao is TipoPublicacao.LIVRO
        assert metadados.ano == 2019

    def test_envia_o_trecho_inicial_no_prompt(self):
        def handler(request: httpx.Request) -> httpx.Response:
            corpo = json.loads(request.content)
            assert "TRECHO INICIAL" in corpo["messages"][1]["content"]
            return httpx.Response(
                200, json={"message": {"content": json.dumps(PAYLOAD_VALIDO)}}
            )

        extrator = ExtratorOllama(config_local(), cliente=cliente_com(handler))
        extrator.extrair(documento("TRECHO INICIAL"))

    def test_corrige_sobrenome_em_minusculas_automaticamente(self):
        # Modelos locais menores tendem a esquecer o SOBRENOME em maiúsculas
        # dentro da referência formatada — o extrator corrige sozinho.
        payload = {
            **PAYLOAD_VALIDO,
            "referencia_abnt": "Frankl, Viktor E. Em busca de sentido. Petrópolis: Vozes, 2019.",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"message": {"content": json.dumps(payload)}})

        extrator = ExtratorOllama(config_local(), cliente=cliente_com(handler))
        metadados = extrator.extrair(documento())

        assert metadados.referencia_abnt.startswith("FRANKL, Viktor E.")

    def test_descarta_identificador_que_nao_esta_no_texto_fonte_automaticamente(self):
        # Caso real observado com um modelo local maior: reconheceu o autor
        # certo por conhecimento próprio, mas completou um DOI fabricado.
        payload = {**PAYLOAD_VALIDO, "identificadores": {
            "isbn": None, "issn": None, "doi": "10.1000/fabricado",
        }}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"message": {"content": json.dumps(payload)}})

        extrator = ExtratorOllama(config_local(), cliente=cliente_com(handler))
        metadados = extrator.extrair(documento())

        assert metadados.identificadores.doi is None

    def test_ollama_fora_do_ar_vira_erro_fatal(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("recusado", request=request)

        extrator = ExtratorOllama(config_local(), cliente=cliente_com(handler))

        with pytest.raises(ErroFatalDeAPI, match="conectar"):
            extrator.extrair(documento())

    def test_modelo_nao_baixado_vira_erro_fatal(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": "model not found"})

        extrator = ExtratorOllama(config_local(), cliente=cliente_com(handler))

        with pytest.raises(ErroFatalDeAPI, match="ollama pull"):
            extrator.extrair(documento())

    def test_json_invalido_vira_erro_de_extracao(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"message": {"content": "não é json"}})

        extrator = ExtratorOllama(config_local(), cliente=cliente_com(handler))

        with pytest.raises(ErroDeExtracao, match="JSON"):
            extrator.extrair(documento())

    def test_payload_fora_do_esquema_vira_erro_de_extracao(self):
        invalido = {**PAYLOAD_VALIDO, "tipo_publicacao": "Panfleto"}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"message": {"content": json.dumps(invalido)}})

        extrator = ExtratorOllama(config_local(), cliente=cliente_com(handler))

        with pytest.raises(ErroDeExtracao, match="esquema"):
            extrator.extrair(documento())

    def test_conteudo_vazio_vira_erro_de_extracao(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"message": {"content": ""}})

        extrator = ExtratorOllama(config_local(), cliente=cliente_com(handler))

        with pytest.raises(ErroDeExtracao, match="não devolveu conteúdo"):
            extrator.extrair(documento())

    def test_trecho_vazio_nao_chama_o_ollama(self):
        chamou = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal chamou
            chamou = True
            return httpx.Response(200, json={"message": {"content": "{}"}})

        extrator = ExtratorOllama(config_local(), cliente=cliente_com(handler))
        doc = documento()
        doc.markdown_inicial = "   "

        with pytest.raises(ErroDeExtracao):
            extrator.extrair(doc)
        assert chamou is False
