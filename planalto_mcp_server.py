"""
Servidor MCP — Buscador de Legislação (Planalto) — v2 CORRIGIDA
=================================================================

Correções em relação à v1:

1. ATALHOS COM URL FIXA E VERIFICADA
   Cada atalho (CF, CLT, CC...) agora aponta para uma LISTA de caminhos
   candidatos reais do Planalto, testados em ordem. O bug do "CC" ocorria
   porque o resolvedor tentava gerar a URL dinamicamente e o padrão de
   leis de 1999-2002 é diferente ("leis/2002/l10406compilada.htm" — com
   subpasta do ano E sufixo "compilada", não "compilado").

2. RESOLUÇÃO DINÂMICA POR ÉPOCA
   Para normas fora dos atalhos, o gerador de candidatos agora conhece
   TODOS os padrões históricos de URL do Planalto:
     - Leis até ~1998:      ccivil_03/leis/l8078.htm | L8078compilado.htm
     - Leis 1999–2002:      ccivil_03/leis/2002/l10406.htm | ...compilada.htm
                            ccivil_03/leis/LEIS_2001/L10270.htm
     - Leis 2003+:          ccivil_03/_ato2003-2006/2003/lei/l10.671.htm
                            (buckets de 4 anos, número COM ponto de milhar,
                             variantes de caixa _Ato/.../Lei/L14133.htm)
     - Decretos-lei:        ccivil_03/decreto-lei/del5452.htm
                            + subpastas 1937-1946 / 1965-1988
     - Leis complementares: ccivil_03/leis/lcp/lcp123.htm
     - Decretos:            ccivil_03/decreto/... e _ato.../decreto/d10.854.htm
     - MPs:                 ccivil_03/_ato.../mpv/mpv1108.htm e mpv/antigas
     - ECs:                 ccivil_03/constituicao/emendas/emc/emc45.htm

3. PARSING DE REFERÊNCIA TOLERANTE
   Aceita indistintamente: "CC", "cc", "código civil", "lei 10406/2002",
   "lei 10.406/2002", "Lei nº 10.406, de 10 de janeiro de 2002",
   "lei n. 10406 de 2002", "LC 123/06", "del 5452", "dl 5.452/43",
   "MP 1108/2022", "EC 45", "emenda constitucional 45/2004" etc.
   Normalização: minúsculas, sem acentos, remove "nº/n.º/no/n.",
   remove pontos do número, expande ano de 2 dígitos.

4. BUSCA DE ARTIGO TOLERANTE
   Aceita "1010", "1.010", "7", "7º", "7o", "7-A", "art. 1.010".
   O extrator indexa TODOS os cabeçalhos "Art. X" do texto (inclusive os
   grafados pelo Planalto como "Art. 1  o", com ordinal separado por
   espaços) e compara números normalizados (sem pontos, sem ordinal),
   fatiando do artigo pedido até o cabeçalho seguinte.

5. CACHE + MENSAGENS DE ERRO ÚTEIS
   URLs resolvidas ficam em cache; páginas baixadas ficam em cache por
   1h. Em caso de falha, o erro lista as URLs tentadas, para diagnóstico.

REQUISITOS:  pip install mcp requests beautifulsoup4
DEPLOY (Render): start command = python planalto_mcp_server.py
                 (usa a var de ambiente PORT automaticamente)
"""

import os
import re
import time
import unicodedata
from typing import Optional

import requests
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP

BASE = "https://www.planalto.gov.br/ccivil_03/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
    )
}
TIMEOUT = 20

mcp = FastMCP(
    "Buscador de Legislação",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 8000)),
)

# ---------------------------------------------------------------------------
# 1. ATALHOS — cada um com lista de caminhos candidatos REAIS, em ordem de
#    preferência (versão compilada primeiro). Corrigido o CC e ampliada a
#    lista com diplomas de uso frequente.
# ---------------------------------------------------------------------------
SHORTCUTS: dict[str, list[str]] = {
    "cf": ["constituicao/constituicao.htm"],
    "clt": ["decreto-lei/del5452compilado.htm", "decreto-lei/del5452.htm"],
    # >>> CORREÇÃO DO BUG: leis de 1999–2002 vivem em leis/<ano>/ e a versão
    # consolidada do CC usa o sufixo "compilada" (feminino).
    "cc": ["leis/2002/l10406compilada.htm", "leis/2002/L10406compilada.htm",
           "leis/2002/l10406.htm"],
    "cpc": ["_ato2015-2018/2015/lei/l13105.htm",
            "_Ato2015-2018/2015/Lei/L13105.htm"],
    "cp": ["decreto-lei/del2848compilado.htm", "decreto-lei/del2848.htm"],
    "cpp": ["decreto-lei/del3689compilado.htm", "decreto-lei/del3689.htm"],
    "cdc": ["leis/l8078compilado.htm", "leis/l8078.htm"],
    "ctn": ["leis/l5172compilado.htm", "leis/l5172.htm"],
    # Extras úteis (não quebram nada; só ampliam a resolução instantânea)
    "eca": ["leis/l8069compilado.htm", "leis/l8069.htm"],
    "lep": ["leis/l7210compilado.htm", "leis/l7210.htm"],
    "lindb": ["decreto-lei/del4657compilado.htm", "decreto-lei/del4657.htm"],
    "lrf": ["leis/lcp/lcp101.htm", "leis/lcp/Lcp101.htm"],
    "ldb": ["leis/l9394compilado.htm", "leis/l9394.htm"],
    "ce": ["leis/l4737compilado.htm", "leis/l4737.htm"],  # Código Eleitoral
}

# Nomes por extenso → atalho
NAME_ALIASES = {
    "constituicao federal": "cf", "constituicao": "cf", "crfb": "cf",
    "cf/88": "cf", "cf 88": "cf",
    "consolidacao das leis do trabalho": "clt",
    "codigo civil": "cc",
    "codigo de processo civil": "cpc", "novo cpc": "cpc",
    "codigo penal": "cp",
    "codigo de processo penal": "cpp",
    "codigo de defesa do consumidor": "cdc",
    "codigo tributario nacional": "ctn",
    "estatuto da crianca e do adolescente": "eca",
    "lei de execucao penal": "lep",
    "lei de introducao": "lindb", "lei de introducao as normas": "lindb",
    "lei de responsabilidade fiscal": "lrf",
    "lei de diretrizes e bases": "ldb",
    "codigo eleitoral": "ce",
}

# ---------------------------------------------------------------------------
# 2. Normalização de texto e da referência
# ---------------------------------------------------------------------------

def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _norm(s: str) -> str:
    s = _strip_accents(s.lower().strip())
    s = s.replace("º", "").replace("°", "")
    # remove "nº", "n.º", "n.", "no ", "num."
    s = re.sub(r"\bn[.\s]*o?\.?\s*(?=\d)", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" .,;")


def _expand_year(y: Optional[str]) -> Optional[int]:
    if not y:
        return None
    y = int(y)
    if y < 100:  # "43" → 1943; "06" → 2006 (corte em 30)
        y += 1900 if y > 30 else 2000
    return y


_TYPE_PATTERNS = [
    ("lc", r"lei complementar|\blcp?\b"),
    ("del", r"decreto[- ]lei|\bdel\b|\bdl\b"),
    ("mpv", r"medida provisoria|\bmpv?\b"),
    ("emc", r"emenda constitucional|\bemc\b|\bec\b"),
    ("dec", r"\bdecreto\b|\bdec\b"),
    ("lei", r"\blei\b"),
]


def parse_reference(referencia: str):
    """Retorna ('shortcut', chave) ou ('norma', tipo, numero, ano) ou None."""
    n = _norm(referencia)

    # 1) atalho direto (cc, clt...) ou nome por extenso
    if n in SHORTCUTS:
        return ("shortcut", n)
    if n in NAME_ALIASES:
        return ("shortcut", NAME_ALIASES[n])
    # nome por extenso com ano junto ("codigo civil de 2002")
    for name, key in NAME_ALIASES.items():
        if n.startswith(name):
            return ("shortcut", key)

    # 2) tipo + número + ano (todos os formatos livres)
    tipo = None
    for t, pat in _TYPE_PATTERNS:
        if re.search(pat, n):
            tipo = t
            break

    m = re.search(r"(\d{1,3}(?:\.\d{3})+|\d+)(?:\s*[-]\s*(\d+))?", n)
    if not m:
        return None
    numero = m.group(1).replace(".", "")
    sufixo = m.group(2)  # MPs reeditadas tipo 2164-41
    if sufixo:
        numero = f"{numero}-{sufixo}"

    ano = None
    rest = n[m.end():]
    # prioridade 1: qualquer ano de 4 dígitos (pega o ÚLTIMO, para não
    # confundir com o dia em "de 10 de janeiro de 2002")
    years4 = re.findall(r"\b(\d{4})\b", rest)
    if years4:
        ano = _expand_year(years4[-1])
    else:
        # prioridade 2: ano de 2 dígitos imediatamente após "/" ("8078/90")
        ym = re.search(r"/\s*(\d{2})\b", rest)
        if ym:
            ano = _expand_year(ym.group(1))

    if tipo is None:
        tipo = "lei"  # número solto: assume lei ordinária
    return ("norma", tipo, numero, ano)


# ---------------------------------------------------------------------------
# 3. Geração de candidatos de URL por tipo/época
# ---------------------------------------------------------------------------

def _dotted(num: str) -> str:
    """10406 -> 10.406 (padrão pós-2003 do Planalto)."""
    if "-" in num:
        base, suf = num.split("-", 1)
        return f"{_dotted(base)}-{suf}"
    try:
        return f"{int(num):,}".replace(",", ".")
    except ValueError:
        return num


def _bucket(ano: int) -> str:
    if ano < 2003:
        return ""
    start = 2003 + ((ano - 2003) // 4) * 4
    return f"{start}-{start + 3}"


def _case_variants(path: str) -> list[str]:
    """Gera a variante com segmentos capitalizados usada em parte do site
    (_Ato2019-2022/2019/Lei/L13874.htm)."""
    parts = path.split("/")
    cap = []
    for p in parts:
        if p.startswith("_ato"):
            cap.append("_Ato" + p[4:])
        elif p in ("lei", "decreto", "mpv"):
            cap.append(p.capitalize())
        elif re.match(r"^[a-z]+[\d.]", p):  # l13874.htm -> L13874.htm
            cap.append(p[0].upper() + p[1:])
        else:
            cap.append(p)
    v = "/".join(cap)
    return [path] if v == path else [path, v]


def candidate_paths(tipo: str, numero: str, ano: Optional[int]) -> list[str]:
    out: list[str] = []
    nd = _dotted(numero)

    def add(*paths):
        for p in paths:
            for v in _case_variants(p):
                if v not in out:
                    out.append(v)

    if tipo == "lei":
        anos = [ano] if ano else []
        # pós-2003 (só se soubermos o ano; sem ano não dá para achar o bucket)
        for a in anos:
            if a and a >= 2003:
                b = _bucket(a)
                for n_ in (nd, numero):
                    add(f"_ato{b}/{a}/lei/l{n_}compilado.htm",
                        f"_ato{b}/{a}/lei/l{n_}.htm")
        # 1999–2002: subpasta do ano
        for a in anos:
            if a and 1999 <= a <= 2002:
                for n_ in (numero, nd):
                    add(f"leis/{a}/l{n_}compilada.htm",
                        f"leis/{a}/l{n_}compilado.htm",
                        f"leis/{a}/l{n_}.htm",
                        f"leis/LEIS_{a}/L{n_}.htm")
        # padrão antigo (sem ano na URL) — vale para a maioria até 1998 e
        # serve de fallback quando o ano não foi informado
        add(f"leis/l{numero}compilado.htm",
            f"leis/l{numero}.htm",
            f"leis/l{nd}.htm")
        # sem ano informado, ainda tenta os buckets recentes mais prováveis
        if not ano and len(numero) >= 5:
            for a in range(2026, 2002, -1):
                b = _bucket(a)
                add(f"_ato{b}/{a}/lei/l{nd}.htm")
                if len(out) > 60:
                    break

    elif tipo == "lc":
        add(f"leis/lcp/lcp{numero}compilado.htm",
            f"leis/lcp/lcp{numero}.htm",
            f"leis/lcp/Lcp{numero}.htm")

    elif tipo == "del":
        add(f"decreto-lei/del{numero}compilado.htm",
            f"decreto-lei/del{numero}.htm",
            f"decreto-lei/1937-1946/del{numero}.htm",
            f"decreto-lei/1965-1988/del{numero}.htm",
            f"decreto-lei/1965-1988/Del{numero}.htm")

    elif tipo == "dec":
        if ano and ano >= 2003:
            b = _bucket(ano)
            for n_ in (nd, numero):
                add(f"_ato{b}/{ano}/decreto/d{n_}.htm")
        add(f"decreto/d{numero}.htm",
            f"decreto/D{numero}.htm",
            f"decreto/d{nd}.htm",
            f"decreto/antigos/d{numero}.htm",
            f"decreto/1990-1994/d{numero}.htm",
            f"decreto/1995-1997/d{numero}.htm")

    elif tipo == "mpv":
        if ano and ano >= 2003:
            b = _bucket(ano)
            add(f"_ato{b}/{ano}/mpv/mpv{numero}.htm")
        add(f"mpv/mpv{numero}.htm",
            f"mpv/{numero}.htm",
            f"mpv/antigas/{numero}.htm",
            f"mpv/Antigas/{numero}.htm")

    elif tipo == "emc":
        add(f"constituicao/emendas/emc/emc{numero}.htm",
            f"constituicao/Emendas/Emc/emc{numero}.htm")

    return out


# ---------------------------------------------------------------------------
# 4. Download com cache + validação
# ---------------------------------------------------------------------------
_page_cache: dict[str, tuple[float, str]] = {}
_resolved: dict[str, str] = {}
_session = requests.Session()
_session.headers.update(HEADERS)


def _fetch(url: str) -> Optional[str]:
    cached = _page_cache.get(url)
    if cached and time.time() - cached[0] < 3600:
        return cached[1]
    try:
        r = _session.get(url, timeout=TIMEOUT)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    # Planalto usa windows-1252/latin-1 na maior parte das páginas antigas
    r.encoding = r.apparent_encoding or "windows-1252"
    html = r.text
    # valida que é mesmo uma página de norma
    if "Art" not in html:
        return None
    text = BeautifulSoup(html, "html.parser").get_text("\n")
    _page_cache[url] = (time.time(), text)
    return text


def resolve_and_fetch(referencia: str):
    """Retorna (texto, url) ou (None, [urls tentadas])."""
    parsed = parse_reference(referencia)
    tried: list[str] = []
    if parsed is None:
        return None, tried

    if parsed[0] == "shortcut":
        key = parsed[1]
        if key in _resolved:
            t = _fetch(_resolved[key])
            if t:
                return t, _resolved[key]
        for path in SHORTCUTS[key]:
            url = BASE + path
            tried.append(url)
            t = _fetch(url)
            if t:
                _resolved[key] = url
                return t, url
        return None, tried

    _, tipo, numero, ano = parsed
    cache_key = f"{tipo}:{numero}:{ano}"
    if cache_key in _resolved:
        t = _fetch(_resolved[cache_key])
        if t:
            return t, _resolved[cache_key]
    for path in candidate_paths(tipo, numero, ano):
        url = BASE + path
        tried.append(url)
        t = _fetch(url)
        if t:
            _resolved[cache_key] = url
            return t, url
    return None, tried


# ---------------------------------------------------------------------------
# 5. Extração do artigo / inciso / alínea
# ---------------------------------------------------------------------------
_ART_HEADER = re.compile(
    r"^[\s\"'(]*Art\s*\.?\s*([\d.]+(?:\s*-\s*[A-Za-z])?)\s*[ºo°]?",
    re.MULTILINE,
)


def _norm_artnum(s: str) -> str:
    return re.sub(r"\s", "", s).replace(".", "").upper()


def extract_article(text: str, artigo: str) -> Optional[str]:
    target = _norm_artnum(_norm(artigo).replace("art", "").strip(" ."))
    headers = []
    for m in _ART_HEADER.finditer(text):
        headers.append((m.start(), _norm_artnum(m.group(1))))
    for i, (pos, num) in enumerate(headers):
        if num == target:
            end = headers[i + 1][0] if i + 1 < len(headers) else len(text)
            chunk = text[pos:end]
            return _clean(chunk)
    return None


def _clean(chunk: str) -> str:
    lines = [ln.strip() for ln in chunk.splitlines()]
    lines = [ln for ln in lines if ln]
    # remove lixo de navegação eventual
    lines = [ln for ln in lines if not re.match(r"^(Presid.ncia|Casa Civil|Subchefia)", ln)]
    out, prev_blank = [], False
    for ln in lines:
        out.append(ln)
    return "\n\n".join(out)


_ROMAN = re.compile(r"^([IVXLCDM]+)\s*[-–]", re.MULTILINE)
_LETTER = re.compile(r"^([a-z])\s*\)", re.MULTILINE)
_PARAG = re.compile(r"^(§+\s*[\d.ºo°]+|Par.grafo\s+.nico)", re.IGNORECASE | re.MULTILINE)


def extract_item(article_text: str, item: str) -> Optional[str]:
    item_n = _norm(item)
    # parágrafo? ("§ 1", "1", "paragrafo unico")
    if item_n.startswith("§") or "paragrafo" in item_n:
        pat = _PARAG
        want = re.sub(r"[^0-9u]", "", item_n) or "u"
    elif re.fullmatch(r"[ivxlcdm]+", item_n):
        pat = _ROMAN
        want = item_n.upper()
    elif re.fullmatch(r"[a-z]", item_n):
        pat = _LETTER
        want = item_n
    else:
        return None

    marks = [(m.start(), m.group(1)) for m in pat.finditer(article_text)]
    all_marks = sorted(
        [(m.start(), "x") for p in (_ROMAN, _LETTER, _PARAG)
         for m in p.finditer(article_text)]
    )
    for pos, label in marks:
        norm_label = _norm_artnum(_strip_accents(label)).upper()
        if pat is _PARAG:
            norm_label = re.sub(r"[^0-9U]", "", norm_label.replace("UNICO", "U")) or "U"
            want_cmp = want.upper()
        elif pat is _LETTER:
            norm_label = norm_label.lower()
            want_cmp = want.lower()
        else:
            want_cmp = want
        if norm_label == want_cmp:
            nxt = [p for p, _ in all_marks if p > pos]
            end = nxt[0] if nxt else len(article_text)
            # inclui o caput (primeira linha do artigo) para contexto
            caput = article_text.split("\n\n", 1)[0]
            return caput + " [...]\n\n" + article_text[pos:end].strip()
    return None


# ---------------------------------------------------------------------------
# 6. Ferramentas MCP
# ---------------------------------------------------------------------------

@mcp.tool()
def buscar_artigo(referencia: str, artigo: str, item: str = "") -> str:
    """Busca o texto de um artigo específico de qualquer norma federal
    brasileira publicada no Planalto (leis, decretos-lei, decretos, leis
    complementares, medidas provisórias, emendas constitucionais e a
    Constituição Federal).

    Args:
        referencia: referência da norma. Aceita atalhos (CF, CLT, CC, CPC,
            CP, CPP, CDC, CTN, ECA, LEP, LINDB, LRF, LDB, CE), nomes por
            extenso ("código civil") ou formato livre em qualquer grafia:
            "lei 8.078/1990", "lei 8078/90", "Lei nº 10.406, de 2002",
            "decreto-lei 5452", "LC 123/2006", "MP 1.108/2022", "EC 45".
            Informar o ano ajuda na resolução de leis pós-2003.
        artigo: número do artigo em qualquer grafia ("7", "7º", "1010",
            "1.010", "7-A").
        item: opcional — inciso (romano: "III"), alínea (letra: "a") ou
            parágrafo ("§ 2", "parágrafo único") para retornar só esse
            trecho. Vazio = artigo completo.
    """
    text, info = resolve_and_fetch(referencia)
    if text is None:
        tried = "\n".join(f"  - {u}" for u in info[:15]) or "  (nenhuma URL gerada)"
        return (
            f"Não localizei '{referencia}' no Planalto. URLs testadas:\n{tried}\n"
            "Dica: informe tipo + número + ano (ex.: 'lei 10.406/2002')."
        )
    url = info
    art = extract_article(text, artigo)
    if art is None:
        return (
            f"Norma encontrada ({url}), mas não localizei o art. {artigo}. "
            "Confira o número (aceito com ou sem pontos: 1010 ou 1.010)."
        )
    if item:
        piece = extract_item(art, item)
        if piece is None:
            return (
                f"Fonte: {url}\n\n{art}\n\n"
                f"[Aviso: não isolei o item '{item}'; artigo completo acima.]"
            )
        return f"Fonte: {url}\n\n{piece}"
    return f"Fonte: {url}\n\n{art}"


@mcp.tool()
def listar_atalhos() -> str:
    """Lista os atalhos de códigos/diplomas de resolução instantânea
    (CF, CLT etc.)."""
    return "\n".join(k.upper() for k in SHORTCUTS)


if __name__ == "__main__":
    mcp.run(transport="sse")
