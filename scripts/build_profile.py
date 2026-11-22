"""Build Bushran N. Iqbal's GitHub profile.

Fetches Google Scholar (metrics, citations per year, publication list) and
OpenAlex (DOIs, open-access status, co-author institutions), caches everything
in data/profile.json, renders the SVG cards in assets/ (light and dark), and
rewrites the generated blocks in README.md.

Every source is optional. If one fails, the last good copy in the cache is
used, so the profile never breaks or goes blank.

Usage:
    python scripts/build_profile.py            # fetch, then render
    python scripts/build_profile.py --offline  # render from the cache only
"""
import datetime as dt
import difflib
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from html import escape
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------- settings
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "profile.json"
ASSETS = ROOT / "assets"
README = ROOT / "README.md"

NAME = "Bushran N. Iqbal"
SELF_SURNAME = "iqbal"
FIELDS = "MEDICAL VIROLOGY  ·  MOLECULAR BIOLOGY  ·  INFECTIOUS DISEASES"
AFFILIATION = "Institute of Biotechnology, Life Sciences Center  ·  Vilnius University"
ROTATING = [  # hero line: "Researching <phrase>"
    "genomic surveillance of SARS-CoV-2",
    "LAMP and RT-PCR molecular diagnostics",
    "coronaviruses in bats and wildlife",
    "dengue virus serotypes and vectors",
]

SCHOLAR_ID = "3r5SETYAAAAJ"
ORCID_ID = "0000-0002-6299-9556"

PROFILES = [  # (file slug, label, brand colour, url)
    ("scholar", "Google Scholar", "#4285F4", f"https://scholar.google.com/citations?user={SCHOLAR_ID}&hl=en"),
    ("orcid", "ORCID", "#A6CE39", f"https://orcid.org/{ORCID_ID}"),
    ("researchgate", "ResearchGate", "#00CCBB", "https://www.researchgate.net/profile/Bushran-N-Iqbal"),
    ("loop", "Frontiers Loop", "#8B5CF6", "https://loop.frontiersin.org/people/2158213/overview"),
    ("scholargps", "ScholarGPS", "#1A7F5A", "https://scholargps.com/scholars/86095595769584/bushran-n-iqbal"),
    ("sciprofiles", "SciProfiles", "#0A6CB0", "https://sciprofiles.com/profile/4598935"),
]

# Research themes are counted automatically from publication titles.
THEMES = [
    ("SARS-CoV-2 & COVID-19", r"sars[- ]?cov|covid|corona virus-2|severe acute respiratory"),
    ("Molecular diagnostics", r"isothermal|rt-pcr|rna extract|rna load|pool|antigen test|molecular protocol|sample-based rna|total rna"),
    ("Zoonotic coronaviruses", r"\bbats?\b|rodent|wildlife|farm|coronaviruses in|identification of coronaviruses"),
    ("Genomic surveillance", r"variant|molecular epidemiology|surveillance"),
    ("Vaccines & immunity", r"vaccin|immunity|seroprevalence|anti-hbs"),
    ("Dengue & vectors", r"dengue|aedes"),
]

# Featured cards: title fragments, in display order. Missing ones are
# replaced by the most cited remaining journal publications.
FEATURED = [
    "real time loop mediated isothermal",
    "molecular epidemiology of sars-cov-2 detected",
    "circulating dengue virus serotypes",
    "quality of the sample-based rna",
]

CONFERENCE_HINTS = r"conference|congress|symposium|sessions|exposition|association|meeting|workshop|proceedings|icsusl"

SANS = "'Segoe UI',-apple-system,BlinkMacSystemFont,'Helvetica Neue',Helvetica,Arial,sans-serif"
SERIF = "Georgia,Gelasio,'Times New Roman',Times,serif"

THEME = {
    "light": dict(bg="#ffffff", border="#d0d7de", text="#1f2328", muted="#59636e", faint="#eef1f4",
                  chip="#f6f8fa", s1="#2a78d6", s2="#eb6834", s3="#1baf7a", other="#8c959f"),
    "dark": dict(bg="#0d1117", border="#30363d", text="#e6edf3", muted="#9198a1", faint="#1c2128",
                 chip="#161b22", s1="#3987e5", s2="#d95926", s3="#199e70", other="#6e7681"),
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


# ---------------------------------------------------------------- fetching
def _int(text):
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else 0


def fetch_scholar():
    """Google Scholar via SerpApi when SERPAPI_KEY is set, else the public page."""
    key = os.environ.get("SERPAPI_KEY")
    if key:
        r = requests.get("https://serpapi.com/search.json", timeout=60, params={
            "engine": "google_scholar_author", "author_id": SCHOLAR_ID,
            "hl": "en", "num": 100, "api_key": key})
        r.raise_for_status()
        j = r.json()
        if "error" in j:
            raise RuntimeError(j["error"])
        table = {list(row)[0]: list(row.values())[0] for row in j["cited_by"]["table"]}
        since_key = next(k for k in table["citations"] if k.startswith("since_"))
        metrics = {
            "citations": table["citations"]["all"], "citations_since": table["citations"][since_key],
            "h_index": table["h_index"]["all"], "h_index_since": table["h_index"][since_key],
            "i10_index": table["i10_index"]["all"], "i10_index_since": table["i10_index"][since_key],
            "since_year": int(since_key.split("_")[1]),
        }
        graph = {str(g["year"]): g.get("citations", 0) for g in j["cited_by"].get("graph", [])}
        articles = [{
            "title": a.get("title", ""), "authors": a.get("authors", ""),
            "venue": a.get("publication", ""), "year": _int(a.get("year")) or None,
            "cited_by": _int(str((a.get("cited_by") or {}).get("value") or 0)),
        } for a in j.get("articles", [])]
        return {"metrics": metrics, "graph": graph, "articles": articles}

    r = requests.get("https://scholar.google.com/citations", timeout=30,
                     headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
                     params={"user": SCHOLAR_ID, "hl": "en", "cstart": 0, "pagesize": 100})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    cells = [_int(td.text) for td in soup.select("#gsc_rsb_st td.gsc_rsb_std")]
    if len(cells) != 6:
        raise RuntimeError("Scholar returned no metrics (probably a CAPTCHA). Add SERPAPI_KEY.")
    heads = soup.select("#gsc_rsb_st th.gsc_rsb_sth")
    since = _int(heads[-1].text) if heads else None
    metrics = dict(zip(["citations", "citations_since", "h_index", "h_index_since",
                        "i10_index", "i10_index_since"], cells), since_year=since)
    years = [y.text for y in soup.select(".gsc_g_t")]
    counts = [0] * len(years)
    for bar in soup.select("a.gsc_g_a"):
        z = re.search(r"z-index:\s*(\d+)", bar.get("style", ""))
        if z and int(z.group(1)) <= len(years):
            counts[-int(z.group(1))] = _int(bar.select_one(".gsc_g_al").text)
    articles = []
    for row in soup.select("tr.gsc_a_tr"):
        gray = row.select(".gs_gray")
        venue = gray[1].text if len(gray) > 1 else ""
        articles.append({
            "title": row.select_one(".gsc_a_at").text,
            "authors": gray[0].text if gray else "",
            "venue": venue,
            "year": _int(row.select_one(".gsc_a_h").text) or None,
            "cited_by": _int(row.select_one(".gsc_a_ac").text),
        })
    return {"metrics": metrics, "graph": dict(zip(years, counts)), "articles": articles}


def fetch_openalex():
    """OpenAlex author (found by ORCID iD) and all their works."""
    params = {}
    if os.environ.get("OPENALEX_API_KEY"):
        params["api_key"] = os.environ["OPENALEX_API_KEY"]
    base = "https://api.openalex.org"
    author = requests.get(f"{base}/authors/orcid:{ORCID_ID}", params=params, timeout=30)
    author.raise_for_status()
    author = author.json()
    aid = author["id"].rsplit("/", 1)[-1]
    works = requests.get(f"{base}/works", timeout=60, params={
        **params, "filter": f"author.id:{aid}", "per_page": 100,
        "select": "title,publication_year,doi,cited_by_count,open_access,primary_location,authorships,type"})
    works.raise_for_status()
    out = []
    for w in works.json().get("results", []):
        if not w.get("title"):
            continue
        source = ((w.get("primary_location") or {}).get("source") or {})
        out.append({
            "title": re.sub(r"<[^>]+>", "", w["title"]), "year": w.get("publication_year"), "doi": w.get("doi"),
            "cited_by": w.get("cited_by_count", 0),
            "is_oa": bool((w.get("open_access") or {}).get("is_oa")),
            "venue": source.get("display_name") or "",
            "authors": [[a["author"]["display_name"], [i["display_name"] for i in a.get("institutions", [])]]
                        for a in w.get("authorships", [])],
        })
    counts = {str(c["year"]): c["cited_by_count"] for c in author.get("counts_by_year", [])}
    return {"counts_by_year": counts, "works": out}


# ---------------------------------------------------------------- merging
def norm(title):
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"[-–—]", " ", (title or "").lower())).strip()


def match_openalex(title, works):
    t = norm(title).rstrip(" .")
    best, score = None, 0.0
    for w in works:
        o = norm(w["title"])
        s = difflib.SequenceMatcher(None, t, o[:len(t) + 5] if title.endswith(("…", "...")) else o).ratio()
        if s > score:
            best, score = w, s
    return best if score >= 0.85 else None


def clean_venue(venue):
    """Turns 'Viruses 17 (9), 1189, 2025' into 'Viruses'. Drops truncation marks and dangling brackets."""
    v = re.sub(r"\s*(…|\.\.\.)\s*$", "", venue or "").strip()
    v = re.sub(r",\s*\d{4}$", "", v)
    if v.count("(") > v.count(")"):
        v = v[:v.rfind("(")].strip()
    return re.split(r"\s\d+\s*(?:\(|,|$)", v)[0].strip()


def short_name(full):
    parts = [p for p in re.split(r"[\s.]+", full.strip()) if p]
    if len(parts) < 2:
        return full
    initials = "".join(p if (p.isupper() and len(p) <= 4) else p[0].upper() for p in parts[:-1])
    return f"{initials} {parts[-1]}"


def build_publications(cache):
    """One list: Scholar entries enriched with OpenAlex DOIs, OA flags and full author lists."""
    works = cache["openalex"]["works"]
    articles = cache["scholar"]["articles"] or [
        {"title": w["title"], "authors": "", "venue": w["venue"], "year": w["year"], "cited_by": w["cited_by"]}
        for w in works]
    pubs = []
    for a in articles:
        w = match_openalex(a["title"], works)
        venue = (w or {}).get("venue") or clean_venue(a["venue"])
        authors = ", ".join(short_name(n) for n, _ in w["authors"]) if w else a["authors"]
        kind = "journal" if w or not re.search(CONFERENCE_HINTS, a["venue"] or "", re.I) else "conference"
        pubs.append({
            "title": (w or a)["title"].rstrip(), "authors": authors, "venue": venue,
            "year": a["year"] or (w or {}).get("year"), "cited_by": a["cited_by"],
            "doi": (w or {}).get("doi"), "is_oa": (w or {}).get("is_oa", False), "kind": kind,
        })
    pubs.sort(key=lambda p: (-(p["year"] or 0), -p["cited_by"], p["title"]))
    return pubs


def themes_of(title, counts=None):
    """Themes a title belongs to; with counts, the most specific (rarest) theme comes first."""
    found = [name for name, pattern in THEMES if re.search(pattern, title, re.I)]
    return sorted(found, key=lambda n: counts[n]) if counts else found


def collaboration(cache):
    """Co-author clusters (name variants merged), joint-paper counts and institutions."""
    clusters = []  # dicts: surname, initials(set), name, count, inst Counter, papers set

    def find(full):
        parts = [p for p in re.split(r"[\s.]+", full) if p]
        surname = parts[-1].lower()
        initials = set("".join(p if (p.isupper() and len(p) <= 4) else p[0].upper() for p in parts[:-1]))
        for c in clusters:
            if c["surname"] == surname and (initials <= c["initials"] or c["initials"] <= initials):
                c["initials"] |= initials
                if len(full) > len(c["name"]):
                    c["name"] = full
                return c
        c = {"surname": surname, "initials": initials, "name": full, "inst": Counter(), "papers": set()}
        clusters.append(c)
        return c

    for i, w in enumerate(cache["openalex"]["works"]):
        for name, insts in w["authors"]:
            if SELF_SURNAME in name.lower():
                continue
            c = find(name)
            c["papers"].add(i)
            c["inst"].update(insts)
    for c in clusters:
        c["count"] = len(c["papers"])
        c["home"] = c["inst"].most_common(1)[0][0] if c["inst"] else None
    return clusters


# ---------------------------------------------------------------- svg helpers
CSS = f"""
.sans{{font-family:{SANS}}} .serif{{font-family:{SERIF}}}
@keyframes up{{from{{opacity:0;transform:translateY(8px)}}to{{opacity:1;transform:none}}}}
@keyframes growY{{from{{transform:scaleY(0)}}to{{transform:scaleY(1)}}}}
@keyframes growX{{from{{transform:scaleX(0)}}to{{transform:scaleX(1)}}}}
@keyframes pop{{from{{opacity:0;transform:scale(.3)}}to{{opacity:1;transform:scale(1)}}}}
@keyframes draw{{from{{stroke-dashoffset:1}}to{{stroke-dashoffset:0}}}}
@keyframes fade{{from{{opacity:0}}to{{opacity:1}}}}
.up{{animation:up .7s cubic-bezier(.2,.7,.2,1) both}}
.gy{{transform-box:fill-box;transform-origin:50% 100%;animation:growY .9s cubic-bezier(.2,.7,.2,1) both}}
.gx{{transform-box:fill-box;transform-origin:0 50%;animation:growX .9s cubic-bezier(.2,.7,.2,1) both}}
.pop{{transform-box:fill-box;transform-origin:center;animation:pop .6s cubic-bezier(.3,1.4,.5,1) both}}
.draw{{stroke-dasharray:1;animation:draw 1.2s ease-out both}}
.fade{{animation:fade 1s ease-out both}}
@media (prefers-reduced-motion:reduce){{*{{animation:none!important}}}}
"""


def svg(w, h, title, body, extra_css=""):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'role="img" aria-label="{escape(title)}"><title>{escape(title)}</title>'
            f'<style>{CSS}{extra_css}</style>{body}</svg>')


def delay(i, step=0.08, start=0.1):
    return f'style="animation-delay:{start + i * step:.2f}s"'


def text_w(text, size, k=0.55):
    return len(text) * size * k


def wrap(text, max_chars, max_lines):
    words, lines, line = text.split(), [], ""
    for word in words:
        if len(line) + len(word) + 1 > max_chars and line:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(" ,.:;-") + "…"
    return lines


def top_rounded(x, y, w, h, r=4):
    r = min(r, h, w / 2)
    return (f"M{x:.1f},{y + h:.1f}V{y + r:.1f}Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f}"
            f"H{x + w - r:.1f}Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f}V{y + h:.1f}Z")


def frame(w, h, t):
    return f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="14" fill="{t["bg"]}" stroke="{t["border"]}"/>'


def heading(t, title, subtitle, w):
    return (f'<text x="28" y="40" class="sans" font-size="16" font-weight="600" fill="{t["text"]}">{escape(title)}</text>'
            f'<text x="{w - 28}" y="40" class="sans" font-size="12" text-anchor="end" fill="{t["muted"]}">{escape(subtitle)}</text>')


def fmt_date(iso):
    return dt.date.fromisoformat(iso).strftime("%-d %b %Y") if iso else None


# ---------------------------------------------------------------- cards
def card_hero(stats):
    w, h, cx, cy = 840, 280, 690, 132
    spikes = []
    for i in range(18):
        a = 2 * math.pi * i / 18
        x1, y1 = cx + 60 * math.cos(a), cy + 60 * math.sin(a)
        x2, y2 = cx + 80 * math.cos(a), cy + 80 * math.sin(a)
        spikes.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#7fe3d8" stroke-width="3" stroke-linecap="round"/>'
                      f'<circle cx="{cx + 85 * math.cos(a):.1f}" cy="{cy + 85 * math.sin(a):.1f}" r="6.5" fill="url(#spike)"/>')
    rnp = "".join(f'<circle cx="{cx + r * math.cos(a):.1f}" cy="{cy + r * math.sin(a):.1f}" r="3" fill="#bff5ee" opacity=".35"/>'
                  for r, n in ((22, 7), (42, 12)) for a in [2 * math.pi * k / n for k in range(n)])

    period = 2 * math.pi * 34  # one helix turn, so the scroll loops seamlessly

    def strand(phase, colour, op):
        pts = " ".join(f"{x},{248 + 9 * math.sin(x / 34 + phase):.1f}" for x in range(-220, w + 221, 4))
        return f'<polyline points="{pts}" fill="none" stroke="{colour}" stroke-width="1.6" opacity="{op}"/>'

    rungs = "".join(f'<line x1="{x}" y1="{248 + 9 * math.sin(x / 34):.1f}" x2="{x}" y2="{248 + 9 * math.sin(x / 34 + math.pi):.1f}" '
                    f'stroke="#7fe3d8" stroke-width="1" opacity=".22"/>' for x in range(-218, w + 220, 14))
    hidden = ' opacity="0"'
    phrases = "".join(
        f'<text x="68" y="163" class="sans ph" font-size="17" fill="#7fe3d8"{hidden if i else ""} '
        f'style="animation-delay:{i * 3}s">{escape(p)}</text>' for i, p in enumerate(ROTATING))
    chips, x = [], 48
    for i, label in enumerate(stats):
        cw = text_w(label, 12.5, 0.56) + 26
        chips.append(f'<g class="up" {delay(i, 0.12, 0.6)}><rect x="{x}" y="206" width="{cw:.0f}" height="28" rx="14" '
                     f'fill="#ffffff" fill-opacity=".08" stroke="#7fe3d8" stroke-opacity=".35"/>'
                     f'<text x="{x + cw / 2:.0f}" y="224.5" class="sans" font-size="12.5" text-anchor="middle" fill="#e6fffb">{escape(label)}</text></g>')
        x += cw + 10
    css = f"""
.spin{{transform-box:view-box;transform-origin:{cx}px {cy}px;animation:spin 90s linear infinite}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
.float{{animation:float 7s ease-in-out infinite}}
@keyframes float{{50%{{transform:translateY(-7px)}}}}
.helix{{animation:slide 14s linear infinite}}
@keyframes slide{{to{{transform:translateX(-{period:.2f}px)}}}}
.ph{{animation:cycle {3 * len(ROTATING)}s infinite both}}
@keyframes cycle{{0%{{opacity:0;transform:translateY(6px)}}4%,22%{{opacity:1;transform:none}}26%,100%{{opacity:0;transform:translateY(-6px)}}}}
.pulse{{transform-box:fill-box;transform-origin:center;animation:pulse 4s ease-out infinite}}
@keyframes pulse{{from{{opacity:.5;transform:scale(.8)}}to{{opacity:0;transform:scale(1.5)}}}}
"""
    body = f"""
<defs>
 <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0a1a33"/><stop offset=".55" stop-color="#0d3149"/><stop offset="1" stop-color="#0b4a52"/></linearGradient>
 <radialGradient id="glow"><stop offset="0" stop-color="#3fd1c4" stop-opacity=".35"/><stop offset="1" stop-color="#3fd1c4" stop-opacity="0"/></radialGradient>
 <radialGradient id="core" cx=".38" cy=".35"><stop offset="0" stop-color="#5fe0d2"/><stop offset=".6" stop-color="#1f8ea0"/><stop offset="1" stop-color="#145a73"/></radialGradient>
 <radialGradient id="spike" cx=".35" cy=".35"><stop offset="0" stop-color="#e6fffb"/><stop offset="1" stop-color="#3fd1c4"/></radialGradient>
 <pattern id="dots" width="22" height="22" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r="1.1" fill="#7fe3d8"/></pattern>
 <clipPath id="clip"><rect width="{w}" height="{h}" rx="16"/></clipPath>
</defs>
<g clip-path="url(#clip)">
 <rect width="{w}" height="{h}" fill="url(#bg)"/>
 <rect width="{w}" height="{h}" fill="url(#dots)" opacity=".09"/>
 <circle cx="{cx}" cy="{cy}" r="150" fill="url(#glow)"/>
 <circle cx="{cx}" cy="{cy}" r="92" fill="none" stroke="#7fe3d8" stroke-width="1.5" class="pulse"/>
 <g class="spin">{''.join(spikes)}<circle cx="{cx}" cy="{cy}" r="60" fill="url(#core)"/>{rnp}</g>
 <g class="float" opacity=".55"><circle cx="586" cy="222" r="11" fill="url(#core)"/>
  {''.join(f'<circle cx="{586 + 16 * math.cos(2 * math.pi * k / 10):.1f}" cy="{222 + 16 * math.sin(2 * math.pi * k / 10):.1f}" r="2.6" fill="#7fe3d8"/>' for k in range(10))}</g>
 <g class="float" opacity=".4" style="animation-delay:-3s"><circle cx="806" cy="34" r="7" fill="url(#core)"/>
  {''.join(f'<circle cx="{806 + 11 * math.cos(2 * math.pi * k / 8):.1f}" cy="{34 + 11 * math.sin(2 * math.pi * k / 8):.1f}" r="2" fill="#7fe3d8"/>' for k in range(8))}</g>
 <g class="helix">{rungs}{strand(0, "#7fe3d8", .6)}{strand(math.pi, "#4aa3ff", .5)}</g>
 <text x="48" y="66" class="sans up" font-size="11.5" letter-spacing="2.2" font-weight="600" fill="#7fe3d8">{escape(FIELDS)}</text>
 <text x="46" y="122" class="serif up" font-size="50" fill="#ffffff" style="animation-delay:.15s">{escape(NAME)}</text>
 <circle cx="55" cy="157" r="4" fill="#7fe3d8"/><circle cx="55" cy="157" r="4" fill="none" stroke="#7fe3d8" class="pulse"/>
 {phrases}
 <text x="48" y="190" class="sans up" font-size="13.5" fill="#ffffff" fill-opacity=".65" style="animation-delay:.45s">{escape(AFFILIATION)}</text>
 {''.join(chips)}
</g>"""
    return svg(w, h, f"{NAME}, {FIELDS.title()}", body, css)


def card_metrics(t, m, graph, graph_source, as_of, outputs):
    w, h = 840, 250
    since = m.get("since_year")
    tiles = [
        ("Citations", m["citations"], f"{m['citations_since']} since {since}" if since else ""),
        ("h-index", m["h_index"], f"{m['h_index_since']} since {since}" if since else ""),
        ("i10-index", m["i10_index"], f"{m['i10_index_since']} since {since}" if since else ""),
        ("Research outputs", outputs[0], outputs[1]),
    ]
    body = [frame(w, h, t), heading(t, "Citations and indices", f"Google Scholar{' · as of ' + as_of if as_of else ''}", w)]
    for i, (label, value, sub) in enumerate(tiles):
        x, y = 28 + (i % 2) * 196, 84 + (i // 2) * 86
        body.append(f'<g class="up" {delay(i)}>'
                    f'<text x="{x}" y="{y}" class="sans" font-size="12" fill="{t["muted"]}">{escape(label)}</text>'
                    f'<text x="{x}" y="{y + 40}" class="sans" font-size="40" font-weight="600" fill="{t["text"]}">{value}</text>'
                    f'<text x="{x}" y="{y + 60}" class="sans" font-size="12" fill="{t["muted"]}">{escape(sub)}</text></g>')
    body.append(f'<line x1="426" y1="68" x2="426" y2="222" stroke="{t["border"]}"/>')
    years = sorted(graph, key=int)
    px0, px1, base, top = 456, 812, 206, 96
    body.append(f'<text x="{px0}" y="84" class="sans" font-size="12" fill="{t["muted"]}">Citations per year{" · " + graph_source if graph_source else ""}</text>')
    if years:
        peak = max(max(graph.values()), 1)
        band = (px1 - px0) / len(years)
        bw = min(24, band * 0.5)
        body.append(f'<line x1="{px0}" y1="{base + .5}" x2="{px1}" y2="{base + .5}" stroke="{t["border"]}"/>')
        for i, y in enumerate(years):
            v = graph[y]
            bh = max(2, (base - top) * v / peak) if v else 0
            x = px0 + band * i + (band - bw) / 2
            if bh:
                body.append(f'<path d="{top_rounded(x, base - bh, bw, bh)}" fill="{t["s1"]}" class="gy" {delay(i, .1, .3)}/>')
            body.append(f'<text x="{x + bw / 2:.1f}" y="{base - bh - 7:.1f}" class="sans up" font-size="12" font-weight="600" '
                        f'text-anchor="middle" fill="{t["text"]}" {delay(i, .1, .7)}>{v}</text>'
                        f'<text x="{x + bw / 2:.1f}" y="{base + 18}" class="sans" font-size="11.5" text-anchor="middle" fill="{t["muted"]}">{y}</text>')
    return svg(w, h, "Research impact", "".join(body))


def card_output(t, pubs):
    w, h = 412, 300
    by_year = defaultdict(Counter)
    for p in pubs:
        if p["year"]:
            by_year[p["year"]][p["kind"]] += 1
    years = list(range(min(by_year), max(by_year) + 1)) if by_year else []
    nj, nc = sum(c["journal"] for c in by_year.values()), sum(c["conference"] for c in by_year.values())
    body = [frame(w, h, t), heading(t, "Research output", "per year, by type", w)]
    body.append(f'<rect x="28" y="62" width="10" height="10" rx="2" fill="{t["s1"]}"/>'
                f'<text x="44" y="71.5" class="sans" font-size="12" fill="{t["text"]}">Journal {nj}</text>'
                f'<rect x="130" y="62" width="10" height="10" rx="2" fill="{t["s2"]}"/>'
                f'<text x="146" y="71.5" class="sans" font-size="12" fill="{t["text"]}">Conference {nc}</text>')
    if years:
        px0, px1, base, top = 28, 384, 250, 112
        peak = max(sum(c.values()) for c in by_year.values())
        band = (px1 - px0) / len(years)
        bw = min(24, band * 0.5)
        unit = (base - top) / peak
        body.append(f'<line x1="{px0}" y1="{base + .5}" x2="{px1}" y2="{base + .5}" stroke="{t["border"]}"/>')
        for i, y in enumerate(years):
            c = by_year.get(y, Counter())
            x = px0 + band * i + (band - bw) / 2
            jh, ch = c["journal"] * unit, c["conference"] * unit
            gap = 2 if jh and ch else 0
            parts = []
            if jh:
                parts.append(f'<path d="{top_rounded(x, base - jh, bw, jh, 0 if ch else 4)}" fill="{t["s1"]}"/>')
            if ch:
                parts.append(f'<path d="{top_rounded(x, base - jh - gap - ch, bw, ch)}" fill="{t["s2"]}"/>')
            total = c["journal"] + c["conference"]
            body.append(f'<g class="gy" {delay(i, .1, .2)}>{"".join(parts)}</g>')
            if total:
                body.append(f'<text x="{x + bw / 2:.1f}" y="{base - jh - gap - ch - 7:.1f}" class="sans up" font-size="12" font-weight="600" '
                            f'text-anchor="middle" fill="{t["text"]}" {delay(i, .1, .7)}>{total}</text>')
            body.append(f'<text x="{x + bw / 2:.1f}" y="{base + 18}" class="sans" font-size="11.5" text-anchor="middle" fill="{t["muted"]}">{y}</text>')
    return svg(w, h, "Research output per year", "".join(body))


def card_themes(t, pubs):
    w, h = 412, 300
    counts = Counter(th for p in pubs for th in themes_of(p["title"]))
    rows = [(name, counts[name]) for name, _ in THEMES if counts[name]]
    rows.sort(key=lambda r: -r[1])
    body = [frame(w, h, t), heading(t, "Research themes", "outputs per theme", w)]
    peak = max([c for _, c in rows] or [1])
    for i, (name, c) in enumerate(rows[:6]):
        y = 76 + i * 32
        bw = 150 * c / peak
        body.append(f'<text x="28" y="{y + 13}" class="sans" font-size="12.5" fill="{t["text"]}">{escape(name)}</text>'
                    f'<rect x="196" y="{y + 3}" width="150" height="10" rx="5" fill="{t["faint"]}"/>'
                    f'<rect x="196" y="{y + 3}" width="{bw:.1f}" height="10" rx="5" fill="{t["s1"]}" class="gx" {delay(i, .1, .2)}/>'
                    f'<text x="{356}" y="{y + 13}" class="sans" font-size="12.5" font-weight="600" fill="{t["text"]}">{c}</text>')
    body.append(f'<text x="28" y="{h - 18}" class="sans" font-size="11" fill="{t["muted"]}">Counted from titles; one output can cover several themes</text>')
    return svg(w, h, "Research themes", "".join(body))


def card_featured(t, p, counts):
    w, h = 412, 210
    theme = (themes_of(p["title"], counts) or ["Research"])[0]
    lines = wrap(p["title"], 40, 4)
    venue = p["venue"] if len(p["venue"]) <= 36 else p["venue"][:35].rstrip() + "…"
    kicker = f"{venue}  ·  {p['year']}".upper()
    body = [frame(w, h, t),
            f'<rect x="0" y="22" width="4" height="40" rx="2" fill="{t["s1"]}"/>',
            f'<text x="28" y="40" class="sans" font-size="11" letter-spacing="1.2" font-weight="600" fill="{t["s1"]}">{escape(kicker)}</text>']
    for i, line in enumerate(lines):
        body.append(f'<text x="28" y="{70 + i * 23}" class="serif up" font-size="17.5" fill="{t["text"]}" {delay(i, .06)}>{escape(line)}</text>')
    tw = text_w(theme, 11.5, 0.56) + 22
    meta = f'<rect x="28" y="170" width="{tw:.0f}" height="22" rx="11" fill="{t["chip"]}" stroke="{t["border"]}"/>' \
           f'<text x="{28 + tw / 2:.0f}" y="185" class="sans" font-size="11.5" text-anchor="middle" fill="{t["text"]}">{escape(theme)}</text>'
    x = 28 + tw + 14
    cites = f"{p['cited_by']} citation{'s' if p['cited_by'] != 1 else ''}" if p["cited_by"] else ""
    if cites:
        meta += f'<text x="{x:.0f}" y="185" class="sans" font-size="12" fill="{t["muted"]}">{cites}</text>'
        x += text_w(cites, 12) + 16
    if p["is_oa"]:
        meta += (f'<g transform="translate({x:.0f},173)" fill="none" stroke="{t["s3"]}" stroke-width="1.6" stroke-linecap="round">'
                 f'<rect x="1" y="7" width="11" height="9" rx="2" fill="{t["s3"]}" fill-opacity=".15"/><path d="M3.5 7V4.5a3 3 0 0 1 5.8-1"/></g>'
                 f'<text x="{x + 18:.0f}" y="185" class="sans" font-size="12" fill="{t["muted"]}">Open access</text>')
    body.append(meta)
    return svg(w, h, p["title"], "".join(body))


def card_network(t, clusters, n_works):
    w, h, cx, cy, rad = 840, 470, 615, 236, 104
    insts = Counter(c["home"] for c in clusters if c["home"])
    named = [name for name, _ in insts.most_common(2)]
    colour = dict(zip(named, [t["s1"], t["s2"]]))

    def group(c):
        if c["home"] in colour:
            return c["home"]
        return "Other institutions" if c["home"] else "Affiliation not listed"

    labels = named + [g for g in ("Other institutions", "Affiliation not listed") if any(group(c) == g for c in clusters)]
    colour.update({"Other institutions": t["s3"], "Affiliation not listed": t["other"]})
    order = sorted(clusters, key=lambda c: (labels.index(group(c)), -c["count"], c["surname"]))
    pos = {}
    for i, c in enumerate(order):
        a = -math.pi / 2 + 2 * math.pi * i / max(len(order), 1)
        pos[id(c)] = (cx + rad * math.cos(a), cy + rad * math.sin(a), a)

    body = [frame(w, h, t), heading(t, "Collaboration network", "", w),
            f'<text x="28" y="60" class="sans" font-size="12" fill="{t["muted"]}">Co-authors across {n_works} indexed journal publications</text>']
    for i, (v, label) in enumerate([(len(clusters), "co-authors"), (len(insts), "institutions")]):
        body.append(f'<g class="up" {delay(i)}><text x="{28 + i * 120}" y="128" class="sans" font-size="40" font-weight="600" fill="{t["text"]}">{v}</text>'
                    f'<text x="{28 + i * 120}" y="148" class="sans" font-size="12" fill="{t["muted"]}">{label}</text></g>')
    for i, label in enumerate(labels):
        y = 196 + i * 28
        n = sum(1 for c in clusters if group(c) == label)
        body.append(f'<circle cx="34" cy="{y - 4}" r="5" fill="{colour[label]}"/>'
                    f'<text x="48" y="{y}" class="sans" font-size="12.5" fill="{t["text"]}">{escape(label)}</text>'
                    f'<text x="262" y="{y}" class="sans" font-size="12.5" font-weight="600" text-anchor="end" fill="{t["text"]}">{n}</text>')
    body.append(f'<text x="28" y="{h - 24}" class="sans" font-size="11" fill="{t["muted"]}">Line weight shows joint papers. Source: OpenAlex</text>')
    # co-author pairs who share papers, as chords through the centre
    for i, a in enumerate(order):
        for b in order[i + 1:]:
            shared = len(a["papers"] & b["papers"])
            if shared:
                (x1, y1, _), (x2, y2, _) = pos[id(a)], pos[id(b)]
                body.append(f'<path d="M{x1:.1f},{y1:.1f}Q{cx},{cy} {x2:.1f},{y2:.1f}" fill="none" stroke="{t["muted"]}" '
                            f'stroke-opacity="{min(.06 + .05 * shared, .3):.2f}" stroke-width="1" class="fade" {delay(i, .03, .3)}/>')
    for i, c in enumerate(order):
        x, y, _ = pos[id(c)]
        body.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="{colour[group(c)]}" stroke-opacity=".6" '
                    f'stroke-width="{.8 + .45 * c["count"]:.1f}" pathLength="1" class="draw" {delay(i, .04, .1)}/>')
    for i, c in enumerate(order):
        x, y, a = pos[id(c)]
        r = 3.5 + 1.6 * math.sqrt(c["count"])
        body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{colour[group(c)]}" stroke="{t["bg"]}" stroke-width="1.5" class="pop" {delay(i, .04, .5)}/>')
        deg = math.degrees(a)
        flip = math.cos(a) < -1e-6
        lx, ly = cx + (rad + r + 6) * math.cos(a), cy + (rad + r + 6) * math.sin(a)
        body.append(f'<text transform="translate({lx:.1f},{ly:.1f}) rotate({deg + 180 if flip else deg:.1f})" dy="3.8" '
                    f'class="sans fade" font-size="11" text-anchor="{"end" if flip else "start"}" fill="{t["text"]}" '
                    f'{delay(i, .03, .9)}>{escape(short_name(c["name"]))}</text>')
    body.append(f'<circle cx="{cx}" cy="{cy}" r="17" fill="{t["text"]}" stroke="{t["bg"]}" stroke-width="3" class="pop"/>'
                f'<text x="{cx}" y="{cy + 4.5}" class="sans" font-size="12" font-weight="700" text-anchor="middle" fill="{t["bg"]}">BNI</text>')
    return svg(w, h, "Collaboration network", "".join(body))


def card_link(t, label, brand):
    w, h = int(text_w(label, 13, 0.56) + 44), 34
    body = (f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="17" fill="{t["chip"]}" stroke="{t["border"]}"/>'
            f'<circle cx="18" cy="17" r="5" fill="{brand}"/>'
            f'<text x="{(30 + w - 14) / 2:.1f}" y="21.5" class="sans" font-size="13" font-weight="600" text-anchor="middle" '
            f'fill="{t["text"]}">{escape(label)}</text>')
    return svg(w, h, label, body)


# ---------------------------------------------------------------- readme
def md_publication(p):
    authors = ", ".join(f"<b>{a.strip()}</b>" if SELF_SURNAME in a.lower() else a.strip()
                        for a in p["authors"].split(",") if a.strip())
    title = p["title"].replace("[", "(").replace("]", ")")
    head = f"[{title}]({p['doi']})" if p["doi"] else title
    meta = [authors, f"<i>{escape(p['venue'])}</i>" if p["venue"] else ""]
    if p["cited_by"]:
        meta.append(f"{p['cited_by']} citation{'s' if p['cited_by'] != 1 else ''}")
    if p["is_oa"]:
        meta.append("open access")
    return f"- {head}<br><sub>{' · '.join(m for m in meta if m)}</sub>"


def md_publications(pubs):
    out = []
    journal = [p for p in pubs if p["kind"] == "journal"]
    conference = [p for p in pubs if p["kind"] == "conference"]
    year = None
    for p in journal:
        if p["year"] != year:
            year = p["year"]
            out.append(f"\n**{year or 'Undated'}**\n")
        out.append(md_publication(p))
    if conference:
        out.append(f"\n<details>\n<summary><b>Conference presentations ({len(conference)})</b></summary>\n")
        for p in conference:
            out.append(md_publication(p).replace("</sub>", f" · {p['year']}</sub>") if p["year"] else md_publication(p))
        out.append("\n</details>")
    return "\n".join(out).strip()


def replace_block(text, name, content):
    pattern = rf"(<!--{name}:START-->).*?(<!--{name}:END-->)"
    return re.sub(pattern, lambda m: f"{m.group(1)}\n{content}\n{m.group(2)}", text, flags=re.S)


# ---------------------------------------------------------------- main
def write(name, content):
    (ASSETS / name).write_text(content, encoding="utf-8")


def main():
    offline = "--offline" in sys.argv
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    if not offline:
        for key, fetch in (("scholar", fetch_scholar), ("openalex", fetch_openalex)):
            try:
                fresh = fetch()
                if key == "scholar" and not fresh["articles"]:
                    fresh["articles"] = cache["scholar"]["articles"]
                cache[key] = {**fresh, "fetched": today}
                print(f"{key}: updated")
            except Exception as exc:  # keep last good data
                print(f"{key}: skipped, using cached data ({exc})")
        cache["refreshed"] = today
    CACHE.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")

    pubs = build_publications(cache)
    clusters = collaboration(cache)
    scholar = cache["scholar"]
    graph, source = scholar["graph"], ""
    if not graph:
        graph, source = cache["openalex"]["counts_by_year"], "OpenAlex"
    nj = sum(p["kind"] == "journal" for p in pubs)
    outputs = (len(pubs), f"{nj} journal · {len(pubs) - nj} conference")

    featured = []
    for frag in FEATURED:
        hit = next((p for p in pubs if frag in p["title"].lower() and p not in featured), None)
        if hit:
            featured.append(hit)
    for p in sorted((p for p in pubs if p["kind"] == "journal"), key=lambda p: -p["cited_by"]):
        if len(featured) >= 4:
            break
        if p not in featured:
            featured.append(p)

    theme_counts = Counter(th for p in pubs for th in themes_of(p["title"]))
    ASSETS.mkdir(exist_ok=True)
    write("hero.svg", card_hero([f"{len(pubs)} research outputs",
                                 f"{scholar['metrics']['citations']} citations",
                                 f"{len(clusters)} co-authors"]))
    for mode, t in THEME.items():
        write(f"metrics-{mode}.svg", card_metrics(t, scholar["metrics"], graph, source, fmt_date(scholar.get("fetched")), outputs))
        write(f"output-{mode}.svg", card_output(t, pubs))
        write(f"themes-{mode}.svg", card_themes(t, pubs))
        write(f"network-{mode}.svg", card_network(t, clusters, len(cache["openalex"]["works"])))
        for i, p in enumerate(featured, 1):
            write(f"featured-{i}-{mode}.svg", card_featured(t, p, theme_counts))
        for slug, label, brand, _ in PROFILES:
            write(f"link-{slug}-{mode}.svg", card_link(t, label, brand))

    links = "\n".join(
        f'<a href="{p["doi"] or PROFILES[0][3]}"><picture><source media="(prefers-color-scheme: dark)" '
        f'srcset="assets/featured-{i}-dark.svg"><img src="assets/featured-{i}-light.svg" width="49%" '
        f'alt="{escape(p["title"])}"></picture></a>' for i, p in enumerate(featured, 1))
    when = fmt_date(cache.get("refreshed")) or "not yet"
    footer = (f"<sub>Refreshed every week by GitHub Actions · metrics from Google Scholar, "
              f"publication metadata from OpenAlex · last refreshed {when}</sub>")
    text = README.read_text(encoding="utf-8")
    text = replace_block(text, "FEATURED", f'<p align="center">\n{links}\n</p>')
    text = replace_block(text, "PUBLICATIONS", md_publications(pubs))
    text = replace_block(text, "FOOTER", f'<p align="center">{footer}</p>')
    README.write_text(text, encoding="utf-8")
    print(f"Rendered {len(pubs)} publications, {len(clusters)} co-authors, {len(featured)} featured.")


if __name__ == "__main__":
    main()
