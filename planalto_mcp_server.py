"""
Servidor MCP - Conector de Legislação Federal (Planalto)
==========================================================

Expõe duas ferramentas ao Claude:
  - buscar_artigo(referencia, artigo): resolve a URL dinamicamente e
    retorna o texto de um artigo específico de qualquer norma federal
  - listar_atalhos(): lista os atalhos de resolução instantânea (CF, CLT etc.)

REQUISITOS
----------
pip install mcp requests beautifulsoup4 --break-system-packages

COMO RODAR LOCAL (teste rapido, antes de hospedar)
-----------------------------------------------------
python planalto_mcp_server.py
# sobe um servidor HTTP/SSE em http://localhost:8000/sse

COMO HOSPEDAR NO RENDER (free tier)
--------------------------------------
1. Suba esta pasta inteira (este arquivo + requirements.txt) para um
   repositorio no GitHub.
2. No Render (render.com), crie um "New Web Service", conecte o repo.
3. Build command: pip install -r requirements.txt
   Start command: python planalto_mcp_server.py
4. O Render injeta a variavel de ambiente PORT automaticamente - o script
   ja le essa variavel (veja o final do arquivo).
5. Apos o deploy, sua URL do conector sera:
   https://SEU-SERVICO.onrender.com/sse

COMO REGISTRAR NO CLAUDE
-------------------------
- Em claude.ai: Configuracoes > Conectores > Adicionar conector personalizado,
  cole a URL https://SEU-SERVICO.onrender.com/sse

- Claude Desktop / Claude Code (via mcp-remote, ja que eles esperam stdio
  por padrao para conectores remotos):

    {
      "mcpServers": {
        "planalto": {
          "command": "npx",
          "args": ["-y", "mcp-remote", "https://SEU-SERVICO.onrender.com/sse"]
        }
      }
    }

ATENCAO - FREE TIER DORME
----------------------------
O Render free tier hiberna o servico apos ~15 min sem requisicoes. A
primeira chamada depois disso demora ~30s pra "acordar" o servico - e
normal, nao e erro. Chamadas seguintes voltam ao normal.

LIMITAÇÕES CONHECIDAS (leia antes de confiar no resultado)
------------------------------------------------------------
1. O Planalto não tem API de busca nem uma lista única "todas as leis".
   Este servidor resolve a URL dinamicamente, testando os padrões de
   nomenclatura conhecidos (por tipo de norma, número e ano). Isso cobre
   a grande maioria das leis, decretos, decretos-lei, leis complementares,
   MPs e emendas constitucionais — mas normas com convenção de URL fora do
   padrão, ou muito recentes, podem não ser encontradas. Informar o ano
   junto com o número aumenta muito a taxa de acerto.
2. A extração do artigo é feita com regex sobre o texto puro da página.
   Leis com formatação irregular (ex. artigos revogados, redações dadas
   por leis posteriores, notas de rodapé no meio do texto) podem quebrar
   o recorte. SEMPRE confira o resultado contra a URL original antes de
   citar em uma peça.
3. Eu não consegui testar este script contra o site real (meu ambiente de
   execução não tem acesso de rede ao planalto.gov.br). Rode localmente
   e me avise o que quebrar para eu ajustar.
"""

import os
import re
import requests
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP

# O Render define a porta via variável de ambiente PORT. Localmente,
# cai no padrão 8000.
PORTA = int(os.environ.get("PORT", 8000))

mcp = FastMCP("planalto-legislacao", host="0.0.0.0", port=PORTA)

# Atalhos para os códigos/diplomas de uso mais frequente — resolução
# instantânea, sem precisar testar padrões de URL.
ATALHOS = {
    "CF": ("constituicao", None, None),
    "CLT": ("decreto-lei", "5452", "1943"),
    "CC": ("lei", "10406", "2002"),
    "CPC": ("lei", "13105", "2015"),
    "CP": ("decreto-lei", "2848", "1940"),
    "CPP": ("decreto-lei", "3689", "1941"),
    "CDC": ("lei", "8078", "1990"),
    "CTN": ("lei", "5172", "1966"),
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ConectorLegislacaoMCP/1.0)"
}

# Faixas de mandato presidencial usadas pelo Planalto para organizar
# leis/decretos/MPs a partir de 2003 (pasta _ato{inicio}-{fim}).
def _faixa_mandato(ano: int) -> str:
    inicio = 2003 + 4 * ((ano - 2003) // 4)
    fim = inicio + 3
    return f"_ato{inicio}-{fim}"


def _candidatos_url(tipo: str, numero: str, ano: str | None) -> list[str]:
    """
    Gera uma lista de URLs prováveis no Planalto para o tipo/número/ano
    informados, da mais provável para a menos provável. Cobre os padrões
    de nomenclatura usados desde 1930 até hoje.
    """
    base = "https://www.planalto.gov.br/ccivil_03"
    candidatos = []

    if tipo == "constituicao":
        candidatos.append(f"{base}/constituicao/constituicaocompilado.htm")
        return candidatos

    numero_limpo = numero.replace(".", "").replace("/", "")

    if tipo == "decreto-lei":
        candidatos.append(f"{base}/decreto-lei/del{numero_limpo}compilado.htm")
        candidatos.append(f"{base}/decreto-lei/del{numero_limpo}.htm")
        return candidatos

    ano_int = int(ano) if ano else None

    if tipo == "lei_complementar":
        candidatos.append(f"{base}/leis/lcp/lcp{numero_limpo}.htm")
        return candidatos

    if tipo == "emenda_constitucional":
        candidatos.append(f"{base}/constituicao/emendas/emc/emc{numero_limpo}.htm")
        return candidatos

    if tipo == "medida_provisoria":
        if ano_int:
            faixa = _faixa_mandato(ano_int)
            candidatos.append(f"{base}/{faixa}/{ano}/mpv/{numero_limpo}.htm")
            candidatos.append(f"{base}/{faixa}/{ano}/mpv/{numero_limpo}-impressao.htm")
        return candidatos

    if tipo in ("lei", "decreto"):
        prefixo = "l" if tipo == "lei" else "d"
        pasta = "lei" if tipo == "lei" else "decreto"

        candidatos.append(f"{base}/{pasta}s/{prefixo}{numero_limpo}compilado.htm")
        candidatos.append(f"{base}/{pasta}s/{prefixo}{numero_limpo}.htm")

        if ano_int:
            faixa = _faixa_mandato(ano_int)
            candidatos.append(f"{base}/{faixa}/{ano}/{pasta}/{prefixo}{numero_limpo}compilado.htm")
            candidatos.append(f"{base}/{faixa}/{ano}/{pasta}/{prefixo}{numero_limpo}.htm")

    return candidatos


def _resolver_url(tipo: str, numero: str, ano: str | None) -> str | None:
    """Testa os candidatos em ordem e retorna o primeiro que responder 200."""
    for url in _candidatos_url(tipo, numero, ano):
        try:
            resp = requests.head(url, headers=HEADERS, timeout=8, allow_redirects=True)
            if resp.status_code == 200:
                return url
        except requests.RequestException:
            continue
    return None


def _interpretar_referencia(referencia: str) -> tuple[str, str, str | None]:
    """
    Interpreta referências em linguagem natural/abreviada, ex:
    'CF', 'CLT', 'lei 8.078/1990', 'lei complementar 123/2006',
    'decreto-lei 5.452/1943', 'MP 1.108/2022', 'EC 45/2004'.
    Retorna (tipo, numero, ano).
    """
    ref = referencia.strip().upper()

    if ref in ATALHOS:
        return ATALHOS[ref]

    m = re.search(r"\bEC\b\.?\s*(\d+)\s*/?\s*(\d{4})?", ref) or re.search(
        r"EMENDA\s+CONSTITUCIONAL\s*N?º?\s*(\d+)\s*/?\s*(\d{4})?", ref
    )
    if m:
        return ("emenda_constitucional", m.group(1), m.group(2))

    m = re.search(r"LEI\s+COMPLEMENTAR\s*N?º?\s*([\d.]+)\s*/?\s*(\d{4})?", ref) or re.search(
        r"\bLC\b\.?\s*([\d.]+)\s*/?\s*(\d{4})?", ref
    )
    if m:
        return ("lei_complementar", m.group(1), m.group(2))

    m = re.search(r"DECRETO[\s\-]LEI\s*N?º?\s*([\d.]+)\s*/?\s*(\d{4})?", ref)
    if m:
        return ("decreto-lei", m.group(1), m.group(2))

    m = re.search(r"MEDIDA\s+PROVIS[ÓO]RIA\s*N?º?\s*([\d.]+)\s*/?\s*(\d{4})?", ref) or re.search(
        r"\bMP\b\.?\s*([\d.]+)\s*/?\s*(\d{4})?", ref
    )
    if m:
        return ("medida_provisoria", m.group(1), m.group(2))

    m = re.search(r"DECRETO\s*N?º?\s*([\d.]+)\s*/?\s*(\d{4})?", ref)
    if m:
        return ("decreto", m.group(1), m.group(2))

    m = re.search(r"LEI\s*N?º?\s*([\d.]+)\s*/?\s*(\d{4})?", ref)
    if m:
        return ("lei", m.group(1), m.group(2))

    raise ValueError(
        f"Não consegui interpretar a referência '{referencia}'. "
        f"Use formatos como 'lei 8.078/1990', 'decreto-lei 5.452/1943', "
        f"'lei complementar 123/2006', 'MP 1.108/2022', 'EC 45/2004', "
        f"ou os atalhos: {', '.join(ATALHOS.keys())}."
    )


def _baixar_paragrafos(url: str) -> list[str]:
    """
    Retorna os "blocos" de texto da página, divididos por linha em branco.

    Importante: nas páginas do Planalto, os artigos nem sempre estão em
    tags <p> separadas — muitas vezes é tudo texto corrido dentro do
    corpo, e cada artigo vira seu próprio bloco só por causa das quebras
    de linha. Por isso dividimos o texto extraído por linhas em branco,
    em vez de depender da estrutura de tags.
    """
    resp = requests.get(url, headers=HEADERS, timeout=20)
    # Forçamos ISO-8859-1: a detecção automática (chardet) erra com
    # frequência nessas páginas antigas do Planalto, produzindo caracteres
    # corrompidos (mojibake) em vez do acentuado correto.
    resp.encoding = "ISO-8859-1"
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()

    texto = soup.get_text(separator="\n")
    blocos = re.split(r"\n\s*\n", texto)
    return [b.strip() for b in blocos if b.strip()]


def _extrair_artigo(paragrafos: list[str], artigo: str) -> list[str] | None:
    """
    Localiza o parágrafo cujo TEXTO COMEÇA com 'Art. {artigo}' (não uma
    menção no meio do texto) e junta com os parágrafos seguintes até o
    próximo parágrafo que comece com 'Art. {outro número}'.

    Retorna uma LISTA de blocos de texto (parágrafo, inciso, alínea, §),
    já limpos de fragmentação de tags HTML — não uma string única — para
    permitir filtrar por inciso/alínea específica depois.

    Trabalhar em cima dos blocos reais da página evita confundir o artigo
    pedido com referências cruzadas do tipo "(Vide art. 7º, XIII, da
    Constituição Federal)" que aparecem no meio do texto de outros artigos.
    """
    artigo_norm = artigo.strip().upper().replace("º", "").replace("°", "")

    padrao_inicio = re.compile(
        rf"^Art\.?\s*{re.escape(artigo_norm)}(?!\d)", re.IGNORECASE
    )
    padrao_qualquer_artigo = re.compile(r"^Art\.?\s*\d+", re.IGNORECASE)

    indice_inicio = None
    for i, p in enumerate(paragrafos):
        if padrao_inicio.match(p.strip()):
            indice_inicio = i
            break

    if indice_inicio is None:
        return None

    trecho = [paragrafos[indice_inicio]]
    for p in paragrafos[indice_inicio + 1:]:
        p_strip = p.strip()
        if padrao_qualquer_artigo.match(p_strip) and not padrao_inicio.match(p_strip):
            break
        trecho.append(p)
        if len(trecho) > 400:  # corte de segurança pra artigos muito longos
            break

    # A página do Planalto frequentemente quebra o texto em blocos curtos
    # por causa de tags de negrito/itálico, gerando fragmentos de 1-2
    # palavras que não são parágrafos de verdade (ex. "Art. 1", "o", "A").
    # Junta esses fragmentos curtos ao bloco seguinte, mas preserva a
    # quebra de linha real entre incisos, alíneas e parágrafos (§),
    # que são estruturalmente significativos num texto legal.
    LIMITE_FRAGMENTO = 20  # caracteres
    blocos_limpos = []
    buffer = ""
    for b in trecho:
        b_strip = re.sub(r"\s+", " ", b.strip())
        if not b_strip:
            continue
        buffer = f"{buffer} {b_strip}".strip() if buffer else b_strip
        if len(buffer) > LIMITE_FRAGMENTO:
            blocos_limpos.append(buffer)
            buffer = ""
    if buffer:
        blocos_limpos.append(buffer)

    return blocos_limpos


def _filtrar_inciso_ou_alinea(blocos: list[str], referencia_item: str) -> str | None:
    """
    Filtra, dentro dos blocos de um artigo já extraído, o bloco
    correspondente a um inciso (numeração romana: I, II, III, IV-A...)
    ou alínea (letra: a, b, c...).

    Devolve o bloco isolado se encontrado. Não tenta juntar sub-itens
    dentro do inciso/alínea (ex. itens de uma alínea que tenha sub-lista) —
    devolve só o bloco que começa com a marcação pedida.
    """
    ref = referencia_item.strip().upper().rstrip(".-)")

    # Letras que também são numerais romanos válidos (I, V, X, L, C, D, M)
    # são ambíguas — priorizamos a interpretação como INCISO ROMANO,
    # porque é o caso mais comum, e só tratamos como alínea se a
    # referência não for um numeral romano válido.
    eh_romano = bool(re.fullmatch(r"[IVXLCDM]+(-[A-Z])?", ref))

    if eh_romano:
        padrao_romano = re.compile(rf"^{re.escape(ref)}\s*[-–\.]", re.IGNORECASE)
        for b in blocos:
            if padrao_romano.match(b.strip()):
                return b.strip()
        # Não achou como romano — tenta como alínea antes de desistir,
        # cobrindo o caso raro de referência de letra única que não seja
        # numeral romano válido em contexto de alínea.

    if re.fullmatch(r"[A-Z]", ref):
        padrao_alinea = re.compile(rf"^{re.escape(ref)}\s*\)", re.IGNORECASE)
        for b in blocos:
            if padrao_alinea.match(b.strip()):
                return b.strip()

    return None


@mcp.tool()
def listar_atalhos() -> str:
    """Lista os atalhos de códigos/diplomas de resolução instantânea (CF, CLT etc.)."""
    return "\n".join(ATALHOS.keys())


@mcp.tool()
def buscar_artigo(referencia: str, artigo: str, item: str = "") -> str:
    """
    Busca o texto de um artigo específico de qualquer norma federal
    brasileira publicada no Planalto (leis, decretos-lei, decretos,
    leis complementares, medidas provisórias, emendas constitucionais
    e a Constituição Federal).

    A URL é resolvida dinamicamente a partir da referência informada —
    não depende de uma lista fixa, então cobre qualquer norma federal
    cujo padrão de URL o resolvedor reconheça.

    Args:
        referencia: referência da norma. Aceita atalhos (CF, CLT, CC, CPC,
            CP, CPP, CDC, CTN) ou formato livre, ex: "lei 8.078/1990",
            "decreto-lei 5.452/1943", "lei complementar 123/2006",
            "MP 1.108/2022", "EC 45/2004", "decreto 10.854/2021".
            Informar o ano ajuda bastante na resolução para leis/decretos
            pós-2003; sem ano, a busca pode falhar ou demorar mais.
        artigo: número do artigo (ex: "7", "482", "7-A")
        item: opcional — inciso (numeração romana, ex: "III", "XIII") ou
            alínea (letra, ex: "a", "b") para retornar só esse trecho em
            vez do artigo inteiro. Deixe vazio para trazer o artigo completo.
    """
    try:
        tipo, numero, ano = _interpretar_referencia(referencia)
    except ValueError as e:
        return str(e)

    if tipo == "constituicao":
        url = _candidatos_url(tipo, numero, ano)[0]
    else:
        url = _resolver_url(tipo, numero, ano)

    if url is None:
        return (
            f"Não consegui localizar a URL de '{referencia}' no Planalto testando "
            f"os padrões conhecidos. Isso acontece com normas muito antigas, muito "
            f"recentes, ou com convenção de URL fora do padrão. Tente informar o "
            f"ano explicitamente, ou verifique manualmente em "
            f"planalto.gov.br/ccivil_03/legislacao ou legislacao.planalto.gov.br."
        )

    try:
        paragrafos = _baixar_paragrafos(url)
    except requests.RequestException as e:
        return f"Erro ao acessar {url}: {e}"

    blocos = _extrair_artigo(paragrafos, artigo)
    if blocos is None:
        return (
            f"Encontrei a norma em {url}, mas não localizei 'Art. {artigo}' no "
            f"texto — confira manualmente, o artigo pode ter numeração diferente "
            f"(ex. com letra) ou ter sido revogado/renumerado."
        )

    if item.strip():
        trecho_item = _filtrar_inciso_ou_alinea(blocos, item)
        if trecho_item is None:
            return (
                f"Encontrei o Art. {artigo} em {url}, mas não localizei o item "
                f"'{item}' dentro dele — confira manualmente, a numeração pode ser "
                f"diferente do esperado (ex. inciso em vez de alínea)."
            )
        return f"Fonte: {url}\n\n{trecho_item}"

    return f"Fonte: {url}\n\n" + "\n\n".join(blocos)


if __name__ == "__main__":
    mcp.run(transport="sse")
