#!/usr/bin/env python3
"""Construye data/bibliometria.json para el Observatorio UNALM desde OpenAlex (CC0).

Misma estructura que observatorio-unsaac/data/bibliometria.json:
  uni + pares {name, works, cited, h_index, i10, mean_citedness, by_year[{year,works,cited}], openalex}
  + _meta
Claves extra (aditivas, el frontend del UNSAAC las ignora): unsaac (par), ror, temas,
top_autores, impacto, auditoria_afiliacion, caveats.

Definiciones:
  works / cited / h_index / i10 / mean_citedness -> registro de institución OpenAlex
     (mean_citedness = summary_stats.2yr_mean_citedness).
  by_year.works -> trabajos publicados ese año con afiliación a la institución.
  by_year.cited -> (solo UNALM) suma de citas recibidas HASTA HOY por los trabajos publicados ese año
     (por eso cae en años recientes: ventana de citación, no colapso). En los pares = null
     (no disponible: requeriría paginar todos sus trabajos; ver series()).

Uso (venv fuera del repo):  ~/.venvs/observatorio-unalm/bin/python etl/build_bibliometria_unalm.py
"""
import json
import os
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date

API = "https://api.openalex.org"
MAILTO = os.environ.get("OPENALEX_MAILTO", "")  # polite pool de OpenAlex; no hardcodear correos en el repo
YEARS = list(range(2010, date.today().year + 1))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "bibliometria.json")

UNALM = "I79080234"           # ROR 00vr49948 (verificado: único match "La Molina"/"Agraria La Molina" en OpenAlex)
UNALM_ROR = "https://ror.org/00vr49948"
PEERS = {                       # mismas claves que el JSON del UNSAAC + unsaac
    "pucp": "I65285256",
    "cayetano": "I197097402",
    "sanmarcos": "I192513696",
    "unalm": UNALM,
    "upc": "I1311157440",
    "uni_ing": "I62520931",
    "unsaac": "I3132302805",
}

# Afiliación textual esperada para UNALM
OK_RX = re.compile(r"molina|unalm|agrarian univ|agraria|lamolina", re.I)
# Otras "Agrarias" / homónimos que no deben caer en UNALM
SUSP_RX = re.compile(r"selva|tingo mar|nicaragua|managua|altiplano|puno|heredia|costa rica|"
                     r"chile|cuba|habana|colombia|ecuador|bolivia|paraguay|uzbek|armenia|ukrain|"
                     r"kharkiv|georgia|kazakh|kyrgyz|tajik|plovdiv|bulgaria|azerbaij", re.I)


def get(path, params, tries=5):
    params = dict(params, mailto=MAILTO) if MAILTO else dict(params)
    url = f"{API}/{path}?{urllib.parse.urlencode(params, safe=':,|')}"
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "observatorio-unalm-etl"}), timeout=90) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            wait = 2 ** i
            print(f"  retry {i+1} {e} ({wait}s)", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(url)


def fix_moji(t):
    """Corrige mojibake UTF-8 leído como latin-1 (p. ej. 'CÃ©pero' -> 'Cépero')."""
    if t and ("Ã" in t or "Â" in t):
        try:
            return t.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return t
    return t


def page_works(inst, select, extra_filter=""):
    """Paginación básica (page=N). Sin API key OpenAlex no permite cursor= (401) y la
    paginación básica llega hasta 10 000 resultados: suficiente para UNALM (~5.5k)."""
    flt = f"institutions.id:{inst}" + (f",{extra_filter}" if extra_filter else "")
    page, n = 1, 0
    while True:
        d = get("works", {"filter": flt, "select": select, "per-page": 200, "page": page})
        for w in d["results"]:
            yield w
        n += len(d["results"])
        if not d["results"] or n >= d["meta"]["count"] or page * 200 >= 10000:
            break
        page += 1
    print(f"  {inst}: {n} works paginados (count={d['meta']['count']})", file=sys.stderr)


def series(inst):
    """Pares: works por año con group_by (1 llamada). La suma de citas por año de publicación
    exigiría paginar todos sus trabajos (cientos de llamadas; sin API key el presupuesto diario
    OpenAlex por IP es ~1000 llamadas), así que cited queda null para los pares."""
    d = get("works", {"filter": f"institutions.id:{inst},publication_year:{YEARS[0]}-{YEARS[-1]}",
                      "group_by": "publication_year", "per-page": 200})
    g = {int(x["key"]): x["count"] for x in d["group_by"]}
    return [{"year": y, "works": g.get(y, 0), "cited": None} for y in YEARS]


def inst_block(key, inst, by_year):
    r = get(f"institutions/{inst}", {})
    s = r.get("summary_stats") or {}
    return {
        "name": r["display_name"] if key == "uni" else key,
        "works": r["works_count"],
        "cited": r["cited_by_count"],
        "h_index": s.get("h_index"),
        "i10": s.get("i10_index"),
        "mean_citedness": round(s.get("2yr_mean_citedness") or 0, 2),
        "by_year": by_year,
        "openalex": f"https://openalex.org/{inst}",
    }, r


def main():
    today = date.today().isoformat()
    out = {}

    # ---------- UNALM: barrido completo con autorías (temas, autores, impacto, auditoría)
    print("UNALM: barrido completo", file=sys.stderr)
    sel = ("id,publication_year,cited_by_count,type,authorships,primary_topic,open_access,"
           "fwci,citation_normalized_percentile,primary_location,display_name")
    works = list(page_works(UNALM, sel))

    # Lotes sospechosos (registros basura/spam en OpenAlex, p. ej. títulos comerciales atribuidos a
    # una revista con el mismo grupo de autores): mismo source + año + MISMO conjunto de autores,
    # >=10 trabajos y media de citas < 0.2. Un grupo idéntico de autores con 10+ artículos en una
    # sola revista y año casi no ocurre en producción real.
    lots = defaultdict(list)
    for w in works:
        src = ((w.get("primary_location") or {}).get("source") or {})
        team = frozenset((a.get("author") or {}).get("id") for a in (w.get("authorships") or []))
        lots[(src.get("id"), src.get("display_name"), w.get("publication_year"), team)].append(w)
    bad_ids, bad_lots = set(), []
    for (sid, sname, y, team), ws in lots.items():
        if sid and len(ws) >= 10 and team:
            mc = sum((x.get("cited_by_count") or 0) for x in ws) / len(ws)
            if mc < 0.2:
                bad_ids.update(x["id"] for x in ws)
                bad_lots.append({"source": sname, "source_openalex": sid, "year": y, "works": len(ws),
                                 "n_autores_lote": len(team), "citas_media": round(mc, 2),
                                 "titulos_ejemplo": [fix_moji(x.get("display_name") or "")[:90] for x in ws[:3]]})
    bad_lots.sort(key=lambda b: -b["works"])
    auth_bad = Counter()
    yr_w, yr_c = Counter(), Counter()
    fields, subfields, topics = Counter(), Counter(), Counter()
    fields_cit = Counter()
    types, oa = Counter(), Counter()
    fwcis, top10, top1, pct_n = [], 0, 0, 0
    auth_w, auth_c, auth_name, auth_orcid = Counter(), Counter(), {}, {}
    n_auth_hist = Counter()
    mega = {"n": 0, "cited": 0}
    aud = {"autorias_unalm": 0, "autorias_sin_texto": 0, "autorias_texto_ok": 0,
           "autorias_texto_sospechoso": 0, "works_todas_sospechosas": 0, "ejemplos_sospechosos": []}
    peru_flag = Counter()
    for w in works:
        y = w.get("publication_year")
        c = w.get("cited_by_count") or 0
        if y in YEARS:
            yr_w[y] += 1
            yr_c[y] += c
        types[w.get("type") or "otro"] += 1
        oa["abierto" if (w.get("open_access") or {}).get("is_oa") else "cerrado"] += 1
        pt = w.get("primary_topic") or {}
        if pt:
            f = (pt.get("field") or {}).get("display_name")
            fields[f] += 1
            fields_cit[f] += c
            subfields[(pt.get("subfield") or {}).get("display_name")] += 1
            topics[pt.get("display_name")] += 1
        if w.get("fwci") is not None:
            fwcis.append(w["fwci"])
        cnp = w.get("citation_normalized_percentile") or {}
        if cnp:
            pct_n += 1
            top10 += bool(cnp.get("is_in_top_10_percent"))
            top1 += bool(cnp.get("is_in_top_1_percent"))
        auths = w.get("authorships") or []
        na = len(auths)
        n_auth_hist["1" if na <= 1 else "2-5" if na <= 5 else "6-20" if na <= 20 else "21-100" if na <= 100 else ">100"] += 1
        if na > 100:
            mega["n"] += 1
            mega["cited"] += c
        all_susp, any_unalm = True, False
        countries = set()
        for a in auths:
            countries.update(a.get("countries") or [])
            ids = [i.get("id", "").rsplit("/", 1)[-1] for i in a.get("institutions") or []]
            if UNALM not in ids:
                continue
            any_unalm = True
            aud["autorias_unalm"] += 1
            raw = " | ".join(a.get("raw_affiliation_strings") or [])
            if not raw:
                aud["autorias_sin_texto"] += 1
                all_susp = False
            elif OK_RX.search(raw):
                aud["autorias_texto_ok"] += 1
                all_susp = False
            else:
                aud["autorias_texto_sospechoso"] += 1
                if SUSP_RX.search(raw) and len(aud["ejemplos_sospechosos"]) < 12:
                    aud["ejemplos_sospechosos"].append({"work": w["id"], "raw_affiliation": raw[:200]})
            au = a.get("author") or {}
            aid = au.get("id")
            if aid and w["id"] in bad_ids:
                auth_bad[aid] += 1
            elif aid:
                auth_w[aid] += 1
                auth_c[aid] += c
                auth_name[aid] = fix_moji(au.get("display_name"))
                if au.get("orcid"):
                    auth_orcid[aid] = au["orcid"]
        if any_unalm and all_susp:
            aud["works_todas_sospechosas"] += 1
        peru_flag["con_PE" if "PE" in countries else "sin_PE"] += 1

    n = len(works)
    by_year = [{"year": y, "works": yr_w[y], "cited": yr_c[y]} for y in YEARS]
    out["uni"], rec = inst_block("uni", UNALM, by_year)

    # ---------- Pares (misma definición de serie)
    for key, inst in PEERS.items():
        print(f"par {key}", file=sys.stderr)
        by = by_year if inst == UNALM else series(inst)
        out[key], _ = inst_block(key, inst, by)

    # ---------- Top autores (nombres públicos de autores en OpenAlex; sin PII)
    top = []
    for aid, k in auth_w.most_common(25):
        top.append({"author": auth_name[aid], "works_unalm": k, "cited_unalm": auth_c[aid],
                    "openalex": aid, "orcid": auth_orcid.get(aid)})
    # institución "última conocida" de cada top autor (señala si ya no está en UNALM)
    ids = "|".join(a["openalex"].rsplit("/", 1)[-1] for a in top)
    d = get("authors", {"filter": f"ids.openalex:{ids}", "select": "id,last_known_institutions,works_count,summary_stats", "per-page": 50})
    info = {a["id"]: a for a in d["results"]}
    for t in top:
        a = info.get(t["openalex"]) or {}
        lki = a.get("last_known_institutions") or []
        t["ultima_afiliacion"] = lki[0]["display_name"] if lki else None
        t["vigente_unalm"] = any(i.get("id", "").endswith(UNALM) for i in lki)
        t["h_index_total"] = (a.get("summary_stats") or {}).get("h_index")
        t["works_total"] = a.get("works_count")

    # núcleo investigador (receta per cápita): autores con última afiliación UNALM y >=5 trabajos
    nucleo = get("authors", {"filter": f"last_known_institutions.id:{UNALM},works_count:>4", "per-page": 1})["meta"]["count"]

    # ---------- Temas
    def pct(cnt):
        return [{"name": k, "works": v, "pct": round(100 * v / n, 1)} for k, v in cnt.most_common(12) if k]
    out["temas"] = {
        "campos": [dict(x, cited=fields_cit[x["name"]]) for x in pct(fields)],
        "subcampos": pct(subfields),
        "topicos": pct(topics),
        "topicos_institucion": [{"name": t["display_name"], "count": t["count"],
                                 "field": (t.get("field") or {}).get("display_name")} for t in (rec.get("topics") or [])[:12]],
        "nota": "Clasificación automática de OpenAlex (primary_topic por trabajo). No es la estructura de facultades de la UNALM.",
    }

    # ---------- Impacto
    w20 = sum(yr_w[y] for y in range(2020, 2025))
    c20 = sum(yr_c[y] for y in range(2020, 2025))
    out["impacto"] = {
        "works_barridos": n,
        "works_sin_lotes_sospechosos": n - len(bad_ids),
        "citas_por_trabajo": round(sum((w.get("cited_by_count") or 0) for w in works) / n, 2) if n else None,
        "ventana_2020_2024": {"works": w20, "cited": c20, "citas_por_trabajo": round(c20 / w20, 2) if w20 else None},
        "fwci_media": round(statistics.mean(fwcis), 2) if fwcis else None,
        "fwci_mediana": round(statistics.median(fwcis), 2) if fwcis else None,
        "fwci_n": len(fwcis),
        "pct_top10": round(100 * top10 / pct_n, 1) if pct_n else None,
        "pct_top1": round(100 * top1 / pct_n, 1) if pct_n else None,
        "pct_acceso_abierto": round(100 * oa["abierto"] / n, 1) if n else None,
        "tipos": dict(types.most_common(8)),
        "autores_por_trabajo": dict(n_auth_hist),
        "mega_colaboraciones": {"works_mas_100_autores": mega["n"], "citas": mega["cited"],
                                 "pct_citas": round(100 * mega["cited"] / max(1, sum(yr_c.values())), 1)},
        "nucleo_investigador": nucleo,
        "citas_por_investigador": round(out["uni"]["cited"] / nucleo, 1) if nucleo else None,
        "nota": ("FWCI = citas normalizadas por campo, año y tipo (1.0 = promedio mundial). "
                 "Núcleo investigador = autores OpenAlex con última afiliación UNALM y >=5 trabajos: "
                 "NO es el número de docentes. Conteo completo (no fraccional)."),
    }

    aud["works_barridos"] = n
    aud["lotes_sospechosos"] = bad_lots
    aud["works_en_lotes_sospechosos"] = len(bad_ids)
    aud["pct_works_en_lotes_sospechosos"] = round(100 * len(bad_ids) / max(1, n), 1)
    aud["regla_lotes"] = ("Mismo source, año y conjunto idéntico de autores, >=10 trabajos y media de citas < 0.2. "
                          "Se reportan (no se borran de works/by_year, que son cifras de la fuente tal cual).")
    aud["pct_autorias_sospechosas"] = round(100 * aud["autorias_texto_sospechoso"] / max(1, aud["autorias_unalm"]), 2)
    aud["works_con_coautor_peru"] = peru_flag["con_PE"]
    aud["works_sin_coautor_peru"] = peru_flag["sin_PE"]
    aud["ror"] = UNALM_ROR
    aud["homonimos_revisados"] = ("Búsqueda OpenAlex 'La Molina' y 'Agraria La Molina' devuelve una sola institución "
                                  "(I79080234). Se revisa el texto crudo de afiliación de cada autoría UNALM buscando "
                                  "'Molina/UNALM/Agraria/Agrarian University'; las que no lo contienen se cuentan como sospechosas.")
    out["auditoria_afiliacion"] = aud
    out["ror"] = UNALM_ROR
    out["top_autores"] = {
        "lista": top,
        "autores_con_registros_excluidos": len(auth_bad),
        "works_excluidos": len(bad_ids),
        "nota": ("Autores con más trabajos firmados con afiliación UNALM en OpenAlex (desambiguación automática: "
                 "puede fusionar o partir homónimos). Incluye coautores externos con afiliación UNALM en algún trabajo; "
                 "'vigente_unalm' indica si su última afiliación conocida es UNALM. No es un ranking de docentes. "
                 "Se excluyen del conteo los trabajos en lotes sospechosos (registros basura/spam atribuidos por "
                 "error de metadatos de la fuente; no implican conducta de los autores). Un mismo investigador "
                 "puede aparecer partido en dos perfiles OpenAlex."),
    }
    out["caveats"] = [
        "Fuente OpenAlex (abierta, no curada a mano): cobertura y afiliaciones difieren de Scopus/WoS; usar como indicador exploratorio, no ranking oficial (ver CWTS Leiden Ranking Open, SCImago).",
        "Las citas por año de publicación caen en años recientes por ventana de citación (siguen acumulándose), no por menor calidad.",
        "Mayor número de publicaciones = mayor VOLUMEN, no necesariamente mayor impacto; comparar con citas por trabajo y FWCI.",
        "h-index e i10 son acumulados históricos y dependen del tamaño y antigüedad de la institución.",
        "Conteo completo: un trabajo con autores de varias universidades cuenta entero para cada una (no fraccional); las mega-colaboraciones inflan citas.",
        "mean_citedness = media de citas a 2 años de OpenAlex (summary_stats.2yr_mean_citedness).",
        "OpenAlex contiene registros basura (p. ej. títulos comerciales en portugués atribuidos a una revista peruana en 2019); se detectan por regla y se reportan en auditoria_afiliacion.lotes_sospechosos. Las cifras works/by_year/h-index de la fuente los incluyen tal cual.",
        "Totales de la institución (works) y suma de la serie no coinciden: el registro de institución incluye años previos a 2010 y se actualiza con distinto calendario que la búsqueda de trabajos.",
        "Picos atípicos por año pueden deberse a cambios de indexación de la fuente (p. ej. repositorios o tesis) y se reportan tal cual.",
    ]
    out["_meta"] = {
        "fuente": "OpenAlex API (open, CC0)",
        "extraido": today,
        "nota": (f"Datos bibliométricos reales. UNALM = OpenAlex {UNALM} / ROR 00vr49948. "
                 "works/cited/h/i10 del registro de institución; by_year = trabajos publicados por año y citas "
                 "acumuladas a la fecha de corte de esos trabajos (solo UNALM; en los pares by_year.cited = null, no disponible, "
                 "y by_year.works sale de group_by=publication_year). Script: etl/build_bibliometria_unalm.py"),
        "alcance": f"Trabajos con afiliación institucional asignada por OpenAlex; serie {YEARS[0]}-{YEARS[-1]} ({YEARS[-1]} parcial).",
    }
    # mismo orden de claves que el JSON del UNSAAC primero
    order = ["uni", "pucp", "cayetano", "sanmarcos", "unalm", "upc", "uni_ing", "unsaac", "ror",
             "temas", "impacto", "top_autores", "auditoria_afiliacion", "caveats", "_meta"]
    out = {k: out[k] for k in order}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"OK -> {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
