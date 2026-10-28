"""Catálogo taxonômico multi-domínio (Assunto Principal -> Especificação) para o motor ponderado.

Cada entrada de ``CATALOGO_TAXONOMICO`` é um :class:`EntradaTaxonomica`. Os termos são
escritos já sem acentos e em minúsculas; o motor (``metadata_api.infer_thematic_area``)
normaliza o texto de entrada da mesma forma antes de comparar por palavra inteira.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class EntradaTaxonomica:
    area: str
    subarea: str
    rule: str
    keywords: Tuple[str, ...]
    authors: Tuple[str, ...] = ()
    cdd: Tuple[str, ...] = ()
    umbrella: bool = False


def _e(area, subarea, rule, kws, authors="", cdd="", umbrella=False) -> EntradaTaxonomica:
    def _split(s: str) -> Tuple[str, ...]:
        return tuple(x.strip() for x in s.split(",") if x.strip())

    return EntradaTaxonomica(area, subarea, rule, _split(kws), _split(authors), _split(cdd), umbrella)


# Faixas CDD (3 primeiros dígitos) -> macroárea (+4 de desempate para as subáreas da área)
MACRO_CDD: Dict[str, List[Tuple[int, int]]] = {
    "Psicologia": [(150, 159)],
    "Administração": [(350, 351), (658, 658)],
    "Direito": [(340, 349)],
    "Ciências da Saúde": [(610, 619)],
    "Filosofia": [(100, 149), (160, 199)],
    "Ciências Sociais": [(300, 329)],
    "Educação": [(1, 1), (370, 379)],
    "Computação": [(4, 6)],
    "Economia": [(330, 339)],
    "Linguística, Letras e Artes": [(400, 499), (700, 899)],
}

# Descritores acadêmicos em inglês (OpenAlex/Crossref/Google Books/OpenLibrary) -> descritor canônico PT-BR.
# Chaves normalizadas (ASCII, minúsculas, sem hífens). Usado APENAS na normalização de ``subjects``.
ALIASES_ASSUNTOS_INGLES: Dict[str, str] = {
    # Psicologia
    "neuropsychology": "neuropsicologia",
    "neuropsychological assessment": "avaliacao neuropsicologica",
    "cognitive behavioral therapy": "terapia cognitivo comportamental",
    "cognitive therapy": "terapia cognitiva",
    "dialectical behavior therapy": "terapia comportamental dialetica",
    "acceptance and commitment therapy": "terapia de aceitacao e compromisso",
    "behavior analysis": "analise do comportamento",
    "applied behavior analysis": "analise do comportamento aplicada",
    "behaviorism": "behaviorismo",
    "psychoanalysis": "psicanalise",
    "psychoanalytic theory": "psicanalise",
    "analytical psychology": "psicologia analitica",
    "gestalt therapy": "gestalt terapia",
    "humanistic psychology": "psicologia humanista",
    "phenomenological psychology": "fenomenologia",
    "existential psychology": "psicologia existencial",
    "psychometrics": "psicometria",
    "psychological assessment": "avaliacao psicologica",
    "psychological testing": "testes psicologicos",
    "psychopathology": "psicopatologia",
    "mental disorders": "transtornos mentais",
    "autism": "autismo",
    "autism spectrum disorder": "transtorno do espectro autista",
    "attention deficit hyperactivity disorder": "tdah",
    "developmental psychology": "psicologia do desenvolvimento",
    "child development": "desenvolvimento infantil",
    "educational psychology": "psicologia educacional",
    "school psychology": "psicologia escolar",
    "industrial and organizational psychology": "psicologia organizacional",
    "organizational behavior": "comportamento organizacional",
    "occupational health": "saude ocupacional",
    "forensic psychology": "psicologia juridica",
    "health psychology": "psicologia da saude",
    "palliative care": "cuidados paliativos",
    "social psychology": "psicologia social",
    "community psychology": "psicologia comunitaria",
    "environmental psychology": "psicologia ambiental",
    "psychopharmacology": "psicofarmacologia",
    "clinical psychology": "psicologia clinica",
    "psychotherapy": "psicoterapia",
    "psychology": "psicologia",
    "clinical psychiatry": "psiquiatria clinica",
    "biological psychiatry": "psiquiatria biologica",
    # Administração
    "marketing": "marketing",
    "consumer behavior": "comportamento do consumidor",
    "brand management": "gestao de marcas",
    "human resource management": "gestao de pessoas",
    "personnel management": "recursos humanos",
    "corporate finance": "financas corporativas",
    "financial management": "administracao financeira",
    "accounting": "contabilidade geral",
    "strategic management": "planejamento estrategico",
    "business administration": "administracao geral",
    "entrepreneurship": "empreendedorismo",
    "operations management": "gestao de operacoes",
    "supply chain management": "cadeia de suprimentos",
    "project management": "gestao de projetos",
    "public administration": "administracao publica",
    "public policy": "politicas publicas",
    # Demais domínios
    "neuroscience": "neurociencias",
    "neuroanatomy": "neuroanatomia",
    "neurobiology": "neurobiologia",
    "physiology": "fisiologia",
    "public health": "saude publica",
    "epidemiology": "epidemiologia",
    "nursing": "enfermagem",
    "epistemology": "epistemologia",
    "philosophy of science": "filosofia da ciencia",
    "political philosophy": "filosofia politica",
    "existentialism": "existencialismo",
    "philosophy": "filosofia",
    "anthropology": "antropologia",
    "ethnography": "etnografia",
    "sociology": "sociologia",
    "political science": "ciencia politica",
    "constitutional law": "direito constitucional",
    "criminal law": "direito penal",
    "civil law": "direito civil",
    "labor law": "direito do trabalho",
    "research methodology": "metodologia cientifica",
    "pedagogy": "pedagogia",
    "special education": "educacao especial",
    "artificial intelligence": "inteligencia artificial",
    "machine learning": "aprendizado de maquina",
    "data science": "ciencia de dados",
    "software engineering": "engenharia de software",
    "computer networks": "redes de computadores",
    "algorithms": "algoritmos",
    "macroeconomics": "macroeconomia",
    "microeconomics": "microeconomia",
    "political economy": "economia politica",
    "linguistics": "linguistica",
    "literature": "literatura",
}

CATALOGO_TAXONOMICO: List[EntradaTaxonomica] = [
    # ------------------------------------------------------------------ Psicologia
    _e("Psicologia", "Neuropsicologia", "descritores_neuropsicologia",
       "neuropsicologia, avaliacao neuropsicologica, reabilitacao neuropsicologica, funcoes executivas, "
       "neuropsicologico, neuropsicologica, luria",
        "leandro malloy diniz, malloy diniz, daniel fuentes, eliane correa miotto"),
    _e("Psicologia", "Terapia Cognitivo-Comportamental", "descritores_tcc",
       "terapia cognitivo-comportamental, cognitivo-comportamental, cognitivo comportamental, terapia cognitiva, "
       "tcc, terapia do esquema, esquemas desadaptativos, terapia comportamental dialetica, dbt, "
       "terapia de aceitacao e compromisso, terapia baseada em processos, questionamento socratico, "
       "formulacao de casos em tcc",
       "aaron beck, judith beck, paulo knapp, robert leahy, jesse wright, jeffrey young, steven hayes, "
       "marsha linehan, robert friedberg", "616.89142, 616.891425"),
    _e("Psicologia", "Análise do Comportamento", "descritores_analise_comportamento",
       "analise do comportamento, analise comportamental, behaviorismo, behaviorista, comportamento verbal, "
       "contingencias de reforco, analise do comportamento aplicada, aba",
       "b. f. skinner, burrhus frederic skinner, murray sidman, fred keller", "150.1943"),
    _e("Psicologia", "Psicanálise", "descritores_psicanalise",
       "psicanalise, psicanalitica, psicanalitico, metapsicologia, aparelho psiquico, pulsao, complexo de edipo, "
       "clinica lacaniana, inconsciente freudiano, freud, lacan, "
       "alem do principio do prazer, interpretacao dos sonhos, mal estar na civilizacao, "
       "inibicoes sintomas e ansiedade, o ego e o id, tres ensaios sobre a teoria da sexualidade, "
       "o chiste e sua relacao",
       "sigmund freud, jacques lacan, donald winnicott, d. w. winnicott, melanie klein, wilfred bion, "
       "sandor ferenczi, j.-d. nasio, jean laplanche, david zimerman, christian dunker, joel birman, bruce fink, "
       "jorge forbes", "150.195, 616.8917"),
    _e("Psicologia", "Psicologia Analítica", "descritores_junguianos",
       "psicologia analitica, psicologia junguiana, arquetipos, inconsciente coletivo, individuacao, "
       "tipos psicologicos, sombra junguiana, anima e animus, jung, junguiano, junguiana, junguian, "
       "arquetipo, arquetipica, arquetipico, psicologia profunda, psicologia dos complexos, sincronicidade, "
       "mapa da alma, homem e seus simbolos, memorias sonhos reflexoes, a natureza da psique, "
       "psicologia do inconsciente, aspectos do feminino, psicologia e religiao",
       "c. g. jung, carl gustav jung, carl jung, cg jung, c.g. jung, emma jung, murray stein, judith harris, "
       "robin robertson, sallie nichols, marie-louise von franz, von franz, james hillman, edward edinger, "
       "james a. hall, calvin s. hall, frederic lenoir, heraclito pinheiro, verena kast, nise da silveira",
       "150.1954"),
    _e("Psicologia", "Gestalt-Terapia", "descritores_gestalt_terapia",
       "gestalt-terapia, gestalt terapia, clinica gestaltica, abordagem gestaltica, gestaltica, gestaltico, "
       "gestalt, terapia gestalt",
       "fritz perls, frederick perls, perls, laura perls, paul goodman, karina okajima fukumitsu, fukumitsu, "
       "lilian meyer frazao, serge ginger, ginger, erving polster, miriam polster, gary yontef, yontef, "
       "marcos jose muller-granzotto, walter ribeiro", "150.1982"),
    _e("Psicologia", "Fenomenologia", "descritores_fenomenologia",
       "fenomenologia, fenomenologico, fenomenologica, fenomenologicas, humanismo, humanista, "
       "abordagem centrada na pessoa, psicologia humanista, psicologia existencial",
       "carl rogers, adriano furtado holanda, angela ales bello, rolo may, rollo may, irvin yalom"),
    _e("Psicologia", "Logoterapia e Análise Existencial", "descritores_logoterapia",
       "logoterapia, analise existencial, sentido da vida, vontade de sentido, autotranscendencia",
       "viktor frankl, viktor e. frankl, elisabeth lukas"),
    _e("Psicologia", "Psicodrama", "descritores_psicodrama",
       "psicodrama, sociodrama, teatro da espontaneidade, sociometria",
       "j. l. moreno, jacob levy moreno, maria da penha nery, dalmiro bustos"),
    _e("Psicologia", "Terapia Familiar e Sistêmica", "descritores_terapia_familiar_sistemica",
       "terapia familiar, psicoterapia sistemica, terapia relacional sistemica, terapia de casal e familia, "
       "abordagem sistemica, constelacao familiar, ciclo vital da familia",
       "salvador minuchin, murray bowen, virginia satir, maurizio andolfi"),
    _e("Psicologia", "Avaliação Psicológica", "descritores_avaliacao_psicologica_psicometria",
       "avaliacao psicologica, psicometria, psicodiagnostico, instrumentos de avaliacao, testes psicologicos, "
       "testagem psicologica, tecnicas projetivas, wisc, wasi, htp, rorschach, teste palografico, "
       "teste zulliger, escala de autocompaixao", cdd="150.287, 616.89075"),
    _e("Psicologia", "Psicopatologia", "descritores_psicopatologia",
       "psicopatologia, transtornos mentais, dsm-5, dsm-iv, cid-10, cid-11, semiologia psiquiatrica, "
       "transtorno bipolar, transtorno borderline, transtorno de ansiedade, transtorno depressivo, psicopatia, "
       "esquizofrenia, suicidio e prevencao",
       "paulo dalgalarrondo, karl jaspers, david barlow"),
    _e("Psicologia", "Autismo e Neurodesenvolvimento", "descritores_autismo_neurodesenvolvimento",
       "autismo, transtorno do espectro autista, tea, tdah, deficit de atencao, hiperatividade, "
       "neurodesenvolvimento, dupla excepcionalidade, altas habilidades, superdotacao, sindrome de asperger",
       "russell barkley, temple grandin"),
    _e("Psicologia", "Psicologia do Desenvolvimento", "descritores_psicologia_desenvolvimento",
       "psicologia do desenvolvimento, desenvolvimento humano, desenvolvimento infantil, ciclo vital, "
       "psicologia da adolescencia, envelhecimento humano, crianca em desenvolvimento, "
        "desenvolvimento da crianca, psicologia da infancia",
       "diane papalia, jean piaget, lev vygotsky, henri wallon, erik erikson, john bowlby, "
        "helen bee, kathleen berger"),
    _e("Psicologia", "Psicologia da Aprendizagem", "descritores_psicologia_aprendizagem",
       "psicologia da aprendizagem, teorias da aprendizagem, aprendizagem significativa, "
       "dificuldades de aprendizagem, transtornos de aprendizagem",
       "david ausubel, robert gagne, marco antonio moreira"),
    _e("Psicologia", "Psicologia Escolar e Educacional", "descritores_psicologia_escolar",
       "psicologia escolar, psicologia educacional, queixa escolar, medicalizacao da educacao",
       "maria helena souza patto, marilene proenca rebello de souza"),
    _e("Psicologia", "Psicologia Organizacional e do Trabalho", "descritores_psicologia_organizacional",
       "psicologia organizacional, comportamento organizacional, psicologia do trabalho, psicodinamica do trabalho, "
       "saude do trabalhador, saude ocupacional, sindrome de burnout, assedio moral no trabalho, "
       "qualidade de vida no trabalho, orientacao vocacional, orientacao profissional, escolha profissional, "
       "normas regulamentadoras, nr-01, nr-07, nr-09, nr-17, nr-33, nr-35",
       "christophe dejours, stephen robbins, jairo eduardo borges-andrade, jose carlos zanelli, "
       "rosane schleichner levenfus"),
    _e("Psicologia", "Psicologia Jurídica", "descritores_psicologia_juridica",
       "psicologia juridica, psicologia forense, pericia psicologica, inimputabilidade penal, vitimologia, "
       "violencia domestica, alienacao parental, medidas socioeducativas, psicologia criminal, "
        "mediacao de conflitos, mediador de conflitos, conciliacao judicial, psicologo mediador, "
        "atuacao do psicologo enquanto mediador",
       "jose osmir fiorelli, sidney shine, maria antonieta pisano motta"),
    _e("Psicologia", "Psicologia Hospitalar e da Saúde", "descritores_psicologia_hospitalar",
       "psicologia hospitalar, psicologia da saude, cuidados paliativos, psico-oncologia, espiritualidade em saude, "
       "humanizacao hospitalar, luto no hospital",
       "alfredo simonetti, bellkiss wilma romano, valdemar augusto angerami-camon"),
    _e("Psicologia", "Psicologia Social", "descritores_psicologia_social",
       "psicologia social, representacoes sociais, psicologia comunitaria, psicologia social critica, "
       "saude mental coletiva, reforma psiquiatrica, atencao psicossocial, luta antimanicomial, raps, "
       "psicologo no sus, sofrimento etico-politico",
       "silvia lane, ana merces bahia bock, wanderley codo, paulo amarante, serge moscovici, "
       "ignacio martin-baro, badenawa sawaia"),
    _e("Psicologia", "Psicologia Ambiental", "descritores_psicologia_ambiental",
       "psicologia ambiental, relacao pessoa-ambiente, espaco urbano e subjetividade"),
    _e("Psicologia", "Psicofarmacologia", "descritores_psicofarmacologia",
       "psicofarmacologia, psicofarmacos, antidepressivos, antipsicoticos, ansioliticos, estabilizadores de humor",
       "stephen stahl, stephen m. stahl"),
    _e("Psicologia", "História e Ética da Psicologia", "descritores_historia_etica_psicologia",
       "historia da psicologia, matrizes do pensamento psicologico, etica na psicologia, "
       "codigo de etica profissional do psicologo, resolucao cfp, referencias tecnicas para atuacao de psicologas",
       "luis claudio mendonca figueiredo, marina massimi, ana maria jaco-vilela"),
    _e("Psicologia", "Psicologia Clínica", "descritores_psicologia_clinica_saude_mental",
       "psicologia clinica, psicoterapia breve, entrevista clinica, habilidades terapeuticas, plantao psicologico, "
       "aconselhamento psicologico, grupoterapia, psicoterapia, saude mental",
       "aristides volpato cordioli", cdd="616.8914", umbrella=True),
    _e("Psicologia", "Psicologia Geral", "descritores_psicologia_geral", "psicologia", umbrella=True),
    # ------------------------------------------------------------------ Administração
    _e("Administração", "Marketing", "descritores_marketing",
       "marketing, comportamento do consumidor, branding, gestao de marcas, pesquisa de mercado, "
       "posicionamento de mercado, funil de vendas, marketing digital, endomarketing",
       "philip kotler, kevin lane keller, seth godin", "658.8"),
    _e("Administração", "Gestão de Pessoas", "descritores_gestao_pessoas",
       "gestao de pessoas, recursos humanos, administracao de recursos humanos, recrutamento e selecao, "
       "treinamento e desenvolvimento, lideranca empresarial, clima organizacional, avaliacao de desempenho",
       "idalberto chiavenato, joel dutra", "658.3"),
    _e("Administração", "Finanças e Contabilidade", "descritores_financas_contabilidade",
       "financas corporativas, administracao financeira, contabilidade gerencial, contabilidade geral, "
       "demonstracoes contabeis, mercado de capitais, analise de investimentos, controladoria, fluxo de caixa",
       "aswath damodaran, lawrence gitman, eliseu martins", "657, 658.15"),
    _e("Administração", "Estratégia e Gestão", "descritores_estrategia_gestao",
       "estrategia empresarial, planejamento estrategico, vantagem competitiva, governanca corporativa, "
       "empreendedorismo, modelos de negocios, administracao geral, teoria geral da administracao",
       "michael porter, peter drucker, henry mintzberg, alexander osterwalder", "658, 658.4"),
    _e("Administração", "Operações, Projetos e Logística", "descritores_operacoes_projetos_logistica",
       "logistica empresarial, cadeia de suprimentos, supply chain, gestao de operacoes, gestao da producao, "
       "gestao da qualidade, gestao de projetos, pmbok, metodos ageis, lean manufacturing", "", "658.5, 658.7"),
    _e("Administração", "Administração Pública", "descritores_administracao_publica",
       "administracao publica, gestao publica, orcamento publico, politicas publicas", "", "350, 351"),
    # ------------------------------------------------------------------ Ciências da Saúde
    _e("Ciências da Saúde", "Psiquiatria", "descritores_psiquiatria",
       "psiquiatria, compendio de psiquiatria, clinica psiquiatrica, tratado de psiquiatria, "
        "manual de psiquiatria, psiquiatria clinica, psiquiatria biologica, emergencia psiquiatrica",
       "harold kaplan, benjamin sadock", "616.89"),
    _e("Ciências da Saúde", "Neurociências e Neuroanatomia", "descritores_neurociencias",
       "neurociencias, neurociencia, neuroanatomia, neurobiologia, sistema nervoso central, sinapses, "
       "plasticidade cerebral",
       "eric kandel, angelo machado, mark bear, roberto lent, antonio damasio, oliver sacks, ramon cosenza",
       "611.8, 612.8"),
    _e("Ciências da Saúde", "Fisiologia", "descritores_fisiologia",
       "fisiologia humana, fisiologia medica, fisiologia, fisiopatologia, homeostase",
       "arthur guyton, guyton, john e. hall, linda costanzo, dee unglaub silverthorn", "612"),
    _e("Ciências da Saúde", "Saúde Coletiva e Epidemiologia", "descritores_saude_coletiva",
       "saude coletiva, saude publica, epidemiologia, vigilancia em saude, atencao primaria a saude, "
       "sistema unico de saude", "", "614"),
    _e("Ciências da Saúde", "Enfermagem e Cuidado", "descritores_enfermagem",
       "enfermagem, processo de enfermagem, cuidados de enfermagem", "", "610.73"),
    # ------------------------------------------------------------------ Filosofia
    _e("Filosofia", "Filosofia Contemporânea", "descritores_filosofia_contemporanea",
       "filosofia contemporanea, biopolitica, sociedade do cansaco, modernidade liquida, problemas de genero, "
       "genealogia do poder, college de france, foucault",
       "michel foucault, byung-chul han, zygmunt bauman, judith butler, gilles deleuze, felix guattari, "
       "jacques derrida, jurgen habermas, giorgio agamben, slavoj zizek, ailton krenak, djamila ribeiro, "
       "silvia federici"),
    _e("Filosofia", "Fenomenologia e Existencialismo", "descritores_fenomenologia_existencialismo",
       "fenomenologia transcendental, ser e tempo, ontologia fundamental, existencialismo, meditacoes cartesianas, "
       "cartesianische meditationen, husserl, heidegger",
       "edmund husserl, martin heidegger, maurice merleau-ponty, jean-paul sartre, simone de beauvoir, "
       "albert camus, soren kierkegaard"),
    _e("Filosofia", "Epistemologia e Filosofia da Ciência", "descritores_epistemologia",
       "epistemologia, filosofia da ciencia, teoria do conhecimento, logica filosofica, revolucoes cientificas",
       "karl popper, thomas kuhn, gaston bachelard, ludwig wittgenstein"),
    _e("Filosofia", "Ética e Filosofia Moral", "descritores_etica_filosofia_moral",
       "etica filosofica, filosofia moral, etica a nicomaco, fundamentacao da metafisica dos costumes, bioetica, "
       "justica e moral", "peter singer, michael sandel, hans jonas"),
    _e("Filosofia", "Filosofia Política", "descritores_filosofia_politica",
       "filosofia politica, contrato social, o principe, leviata, origens do totalitarismo, manifesto comunista, "
       "o capital",
       "nicolau maquiavel, thomas hobbes, john locke, jean-jacques rousseau, hannah arendt, karl marx, john rawls"),
    _e("Filosofia", "História da Filosofia", "descritores_historia_filosofia",
       "historia da filosofia, filosofia antiga, filosofia medieval, filosofia moderna, estoicismo, os pensadores, "
        "pre socraticos, filosofia pre socratica",
       "platao, aristoteles, socrates, seneca, marco aurelio, epiteto, tomas de aquino, rene descartes, "
       "baruch spinoza, gottfried leibniz, david hume, immanuel kant, g. w. f. hegel, arthur schopenhauer, "
       "friedrich nietzsche, michel de montaigne, voltaire, john stuart mill"),
    # Santo Agostinho permanece em Filosofia Geral por compatibilidade com a suíte legada.
    _e("Filosofia", "Filosofia Geral", "descritores_filosofia",
       "filosofia, filosofica, filosofico, etica, metafisica, nietzsche, kant, hegel, platao, aristoteles, "
       "spinoza, schopenhauer, santo agostinho", "santo agostinho", umbrella=True),
    # ------------------------------------------------------------------ Ciências Sociais
    _e("Ciências Sociais", "Antropologia", "descritores_antropologia",
       "antropologia, antropologia cultural, antropologia social, etnografia, etnologia, interpretacao das culturas, "
       "tristes tropicos",
       "claude levi-strauss, clifford geertz, bronislaw malinowski, franz boas, eduardo viveiros de castro, "
       "presotto", "305.8, 306"),
    _e("Ciências Sociais", "Sociologia", "descritores_sociologia",
       "sociologia, sociologia classica, estratificacao social, movimentos sociais, revolucao burguesa no brasil",
       "max weber, emile durkheim, pierre bourdieu, anthony giddens, florestan fernandes, jesse souza, "
       "gilberto freyre, sergio buarque de holanda", "301"),
    _e("Ciências Sociais", "Ciência Política", "descritores_ciencia_politica",
       "ciencia politica, teoria politica, regimes democraticos, partidos politicos, relacoes internacionais",
       "norberto bobbio, robert dahl", "320"),
    # ------------------------------------------------------------------ Direito
    _e("Direito", "Direito Penal e Criminologia", "descritores_direito_penal",
       "direito penal, processo penal, codigo penal, criminologia, execucao penal, delito", "", "345"),
    _e("Direito", "Direito Civil", "descritores_direito_civil",
       "direito civil, processo civil, codigo civil, contratos, responsabilidade civil, direito de familia, "
       "obrigacoes", "", "346"),
    _e("Direito", "Direito Constitucional", "descritores_direito_constitucional",
       "direito constitucional, constituicao federal, direitos fundamentais, controle de constitucionalidade",
       "", "342"),
    _e("Direito", "Direito do Trabalho", "descritores_direito_trabalho",
       "direito do trabalho, consolidacao das leis do trabalho, clt, processo do trabalho", "", "344"),
    _e("Direito", "Direito Administrativo e Tributário", "descritores_direito_administrativo_tributario",
       "direito administrativo, direito tributario, licitacoes, atos administrativos", "", "343"),
    _e("Direito", "Direito Geral", "descritores_direito",
       "direito, jurisprudencia, legislacao", umbrella=True),
    # ------------------------------------------------------------------ Educação
    _e("Educação", "Metodologia Científica", "descritores_metodologia_cientifica",
       "metodologia cientifica, metodologia da pesquisa, construcao do saber, elaboracao de trabalhos academicos, "
       "pesquisa qualitativa, pesquisa social, normas abnt, projeto de pesquisa, metodos de pesquisa, pesquisa em psicologia, "
        "pesquisa quantitativa, delineamento de pesquisa",
       "christian laville, jean dionne, eva maria lakatos, marina de andrade marconi, antonio carlos gil, "
       "maria cecilia de souza minayo, umberto eco, john creswell, john shaughnessy", "001.42"),
    _e("Educação", "Didática e Pedagogia", "descritores_didatica_pedagogia",
       "pedagogia, didatica, curriculo escolar, pratica educativa, pedagogia do oprimido, formacao docente, "
       "avaliacao da aprendizagem",
       "paulo freire, dermeval saviani, jose carlos libaneo, philippe perrenoud", "370, 371"),
    _e("Educação", "Educação Especial e Inclusiva", "descritores_educacao_especial",
       "educacao inclusiva, educacao especial, atendimento educacional especializado", "", "371.9"),
    _e("Educação", "Educação Geral", "descritores_educacao", "educacao, ensino", umbrella=True),
    # ------------------------------------------------------------------ Computação
    _e("Computação", "Inteligência Artificial e Ciência de Dados", "descritores_ia_ciencia_dados",
       "inteligencia artificial, aprendizado de maquina, machine learning, deep learning, aprendizado por reforco, "
       "redes neurais, ciencia de dados, processamento de linguagem natural", "", "006.3"),
    _e("Computação", "Engenharia de Software", "descritores_engenharia_software",
       "engenharia de software, arquitetura de software, padroes de projeto, refatoracao, testes de software, "
       "desenvolvimento agil",
       "robert c. martin, martin fowler, ian sommerville, eric evans", "005.1"),
    _e("Computação", "Algoritmos e Programação", "descritores_algoritmos_programacao",
       "algoritmos, estruturas de dados, logica de programacao, linguagem python, linguagem java, compiladores",
       "thomas cormen, donald knuth", "005.13"),
    _e("Computação", "Redes, Sistemas e Segurança", "descritores_redes_sistemas_seguranca",
       "redes de computadores, protocolos tcp/ip, sistemas operacionais, sistemas distribuidos, banco de dados, "
       "seguranca da informacao", "andrew tanenbaum, james kurose", "004.6, 005.4, 005.74"),
    # ------------------------------------------------------------------ Economia
    _e("Economia", "Macroeconomia e Microeconomia", "descritores_macro_microeconomia",
       "macroeconomia, microeconomia, teoria economica, politica monetaria, inflacao, principios de economia",
       "gregory mankiw, n. gregory mankiw, paul krugman, hal varian", "339, 338.5"),
    _e("Economia", "Economia Política", "descritores_economia_politica",
       "economia politica, desenvolvimento economico, historia economica, formacao economica",
       "adam smith, david ricardo, thomas piketty, celso furtado", "330, 335"),
    # ------------------------------------------------------------------ Linguística, Letras e Artes
    _e("Linguística, Letras e Artes", "Linguística", "descritores_linguistica",
       "linguistica, sociolinguistica, analise do discurso, fonologia, gramatica",
       "ferdinand de saussure, noam chomsky, mikhail bakhtin", "410, 415"),
    _e("Linguística, Letras e Artes", "Literatura", "descritores_literatura",
       "literatura, teoria literaria, critica literaria, poesia, romance brasileiro",
       "antonio candido, machado de assis", "801, 809, 869"),
    _e("Linguística, Letras e Artes", "Artes", "descritores_artes",
       "artes visuais, historia da arte, teoria da musica, musicologia, cinema, fotografia", "", "700, 709, 780"),
]
