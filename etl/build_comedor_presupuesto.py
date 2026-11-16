#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Presupuesto del comedor universitario / bienestar estudiantil UNALM (pliego 518), 2018-2026.

Fuente: Consulta Amigable MEF (apps5.mineco.gob.pe/transparencia/Navegador/), modo Actividades+Proyectos.

Drill: Año -> Nivel E (Gob. Nacional) -> Sector 10 -> Pliego 518 U.N. AGRARIA LA MOLINA (se busca por nombre) -> ...
  A) ProdProy 3000786 "SERVICIOS ADECUADOS DE APOYO AL ESTUDIANTE" -> AAO (actividades de bienestar)
  B) ... -> AAO 5005863 "BIENESTAR Y ASISTENCIA SOCIAL" -> específica 2.3.2.7.11.5 SERVICIOS DE ALIMENTACION DE
     CONSUMO HUMANO -> Mes   (la UNALM terceriza el comedor: paga un servicio de alimentación, no compra víveres)
  C) Pliego -> 2.3.2.7.11.x (todo el pliego) para comparar

CAVEAT: la Consulta Amigable no muestra el nombre de la META, así que "comedor" se aproxima como
la específica 2.3.2.7.11.5 dentro de la actividad 5005863 BIENESTAR Y ASISTENCIA SOCIAL. Ver _meta.caveats.

Uso: ~/.venvs/observatorio-unalm/bin/python etl/build_comedor_presupuesto.py [desde] [hasta]
Salida: data/comedor-presupuesto-unalm.json (también sirve de caché: años cerrados no se re-scrapean salvo REFRESH=1).
"""
import json, os, re, sys, time, datetime
import requests
from bs4 import BeautifulSoup

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- helpers Consulta Amigable (autocontenidos)
BASE = "https://apps5.mineco.gob.pe/transparencia/Navegador/"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
PLIEGO_RE = r"AGRARIA LA MOLINA"   # el código de pliego se descubre por nombre (Sector 10 Educación)
PLIEGO_COD = {}


def new_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    s.get(BASE + "default.aspx", timeout=40)
    return s


def fields(soup):
    d = {}
    for inp in soup.select("input"):
        n = inp.get("name"); t = (inp.get("type") or "text").lower()
        if not n or t in ("submit", "button", "image", "reset"):
            continue
        if t in ("radio", "checkbox"):
            if inp.has_attr("checked"):
                d[n] = inp.get("value", "on")
        else:
            d[n] = inp.get("value", "")
    for sel in soup.select("select"):
        n = sel.get("name")
        if n:
            o = sel.select_one("option[selected]") or sel.select_one("option")
            d[n] = o.get("value", "") if o else ""
    return d


def rows_with_grp1(soup):
    out = []
    for tr in soup.find_all("tr"):
        radio = tr.find("input", {"name": "grp1"})
        if not radio:
            continue
        lbl = None
        for td in tr.find_all("td"):
            t = td.get_text(" ", strip=True)
            if re.match(r"^[\wÑ]+:\s", t):
                lbl = t; break
        out.append((lbl, radio.get("value")))
    return out


def num(s):
    s = (s or "").replace(",", "").strip()
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def post(s, soup, click_name, click_val, grp1_val, year):
    action = soup.find("form").get("action")
    d = fields(soup)
    d["grp1"] = grp1_val
    d[click_name] = click_val
    r = s.post(BASE + action, data=d,
               headers={"Referer": f"{BASE}Navegar.aspx?y={year}&ap=ActProy"}, timeout=60)
    return BeautifulSoup(r.text, "lxml")

OUT = os.path.join(HERE, "..", "data", "comedor-presupuesto-unalm.json")
# cache = el propio JSON de salida (detalle_anual); no se crean archivos auxiliares
CORTE = "2026-11-16"
COLS = ["pia", "pim", "cert", "comp_anual", "comp_mensual", "dev", "gir", "avance_pct"]
MCOLS = ["cert", "comp_anual", "comp_mensual", "dev", "gir"]
MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
         "Setiembre", "Octubre", "Noviembre", "Diciembre"]

ALIM = [("Generica", "5-23"), ("SubGenerica", "2"), ("SubGenericaDetalle", "7"),
        ("Especifica", "11"), ("EspecificaDetalle", "5")]  # 2.3.2.7.11.5 servicios de alimentación de consumo humano
ALIM_COD = "5"


# ---------------------------------------------------------------- navegación CA
def to_pliego(y):
    s = new_session()
    s.get(f"{BASE}default.aspx?y={y}&ap=ActProy", timeout=40)
    soup = BeautifulSoup(s.get(f"{BASE}Navegar.aspx?y={y}&ap=ActProy", timeout=40).text, "lxml")
    sel = soup.select_one("#ctl00_CPH1_DrpYear option[selected]")
    if not sel or sel.get_text(strip=True) != str(y):
        raise RuntimeError("año no fijado")
    g = soup.select_one("input[name=grp1]").get("value")
    soup = post(s, soup, "ctl00$CPH1$BtnTipoGobierno", "Nivel de Gobierno", g, y)
    e = next(v for l, v in rows_with_grp1(soup) if l and l.strip().upper().startswith("E:"))
    soup = post(s, soup, "ctl00$CPH1$BtnSector", "Sector", e, y)
    sec = next(v for l, v in rows_with_grp1(soup) if l and re.match(r"^0*10\s*:", l.strip()))
    soup = post(s, soup, "ctl00$CPH1$BtnPliego", "Pliego", sec, y)
    lab, pl = next((l, v) for l, v in rows_with_grp1(soup) if l and re.search(PLIEGO_RE, l, re.I))
    PLIEGO_COD[y] = lab
    return s, soup, pl


def table(soup):
    out = []
    for tr in soup.find_all("tr"):
        radio = tr.find("input", {"name": "grp1"})
        if not radio:
            continue
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        lbl = next((c for c in cells if re.match(r"^'?[\wÑ\-]+:\s?", c)), None)
        if lbl is None:
            continue
        i = cells.index(lbl)
        nums = [num(c) for c in cells[i + 1:] if re.match(r"^-?[\d,]+(\.\d+)?$", c)]
        code, _, name = lbl.partition(":")
        out.append({"code": code.strip(), "name": name.strip().lstrip("'"), "grp1": radio.get("value"), "nums": nums})
    return out


def buttons(soup):
    return {i.get("name"): i.get("value") for i in soup.select("input[type=submit]")}


def match(rows, pref):
    """pref: código exacto o 're:REGEX' sobre el nombre."""
    for r in rows:
        if pref.startswith("re:"):
            if re.search(pref[3:], r["name"], re.I):
                return r
        elif r["code"] == pref:
            return r
    return None


def walk(y, steps, final=None, tries=4):
    for attempt in range(tries):
        try:
            s, soup, g = to_pliego(y)
            for btn, pref in steps:
                b = "ctl00$CPH1$Btn" + btn
                soup = post(s, soup, b, buttons(soup)[b], g, y)
                r = match(table(soup), pref)
                if r is None:
                    return None
                g = r["grp1"]
            if final:
                b = "ctl00$CPH1$Btn" + final
                soup = post(s, soup, b, buttons(soup)[b], g, y)
                return table(soup)
            return []
        except Exception as e:  # Incapsula / timeouts
            print(f"   [{y}] {steps[-1:] if steps else ''}->{final} intento {attempt}: {repr(e)[:90]}", flush=True)
            time.sleep(5 + 3 * attempt)
    raise RuntimeError(f"walk falló {y} {steps} {final}")


def rec(r):
    d = dict(zip(COLS, r["nums"]))
    return {"code": r["code"], "name": r["name"], **d}


def mrec(r):
    m = int(r["code"].strip("'"))
    return {"mes": m, "nombre": MESES[m - 1], **dict(zip(MCOLS, r["nums"]))}


APOYO = ("ProdProy", "re:APOYO AL ESTUDIANTE")
BIEN = ("AAO", "re:BIENESTAR Y ASISTENCIA SOCIAL")


def tree(y, prefix, levels, depth=0):
    """Árbol genérica->específica detalle bajo `prefix` (lista de steps)."""
    btn = levels[0]
    rows = walk(y, prefix, btn) or []
    out = []
    for r in rows:
        node = rec(r)
        if len(levels) > 1:
            node["hijos"] = tree(y, prefix + [(btn, r["code"])], levels[1:], depth + 1)
        out.append(node)
        time.sleep(0.5)
    return out


def scrape_year(y, full=False):
    d = {"year": y}
    rows = walk(y, [APOYO], "AAO")
    d["actividades_apoyo_estudiante"] = [rec(r) for r in rows] if rows else []
    rows = walk(y, [], "ProdProy")
    pliego_prod = [rec(r) for r in rows]
    d["producto_apoyo_estudiante"] = next((p for p in pliego_prod if re.search("APOYO AL ESTUDIANTE", p["name"])), None)
    # alimentos consumo humano dentro de Bienestar
    rows = walk(y, [APOYO, BIEN] + ALIM[:-1], "EspecificaDetalle")
    d["bienestar_alimentos_detalle"] = [rec(r) for r in rows] if rows else []
    rows = walk(y, [APOYO, BIEN] + ALIM, "Mes")
    d["bienestar_alimentos_mes"] = [mrec(r) for r in rows] if rows else []
    rows = walk(y, [APOYO, BIEN], "Mes")
    d["bienestar_mes"] = [mrec(r) for r in rows] if rows else []
    rows = walk(y, [APOYO, BIEN], "Generica")
    d["bienestar_generica"] = [rec(r) for r in rows] if rows else []
    # alimentos consumo humano en todo el pliego, por actividad
    rows = walk(y, ALIM[:-1], "EspecificaDetalle")
    d["pliego_alimentos_detalle"] = [rec(r) for r in rows] if rows else []
    if full:
        prods = walk(y, ALIM, "ProdProy") or []
        acts = []
        for p in prods:
            for a in walk(y, ALIM + [("ProdProy", p["code"])], "AAO") or []:
                acts.append({"producto": p["code"] + " " + p["name"], **rec(a)})
        d["pliego_alimentos_por_actividad"] = acts
        d["bienestar_fuente"] = [rec(r) for r in (walk(y, [APOYO, BIEN], "FuenteAgregada") or [])]
        d["bienestar_arbol"] = tree(y, [APOYO, BIEN],
                                    ["Generica", "SubGenerica", "SubGenericaDetalle", "Especifica", "EspecificaDetalle"])
        d["pliego_mes_alimentos"] = [mrec(r) for r in (walk(y, ALIM, "Mes") or [])]
    return d


# ---------------------------------------------------------------- análisis
def g(lst, code):
    return next((x for x in lst if x["code"] == code), None)


def bien(d):
    return next((a for a in d["actividades_apoyo_estudiante"] if "BIENESTAR" in a["name"]), None)


def alim_bien(d):
    return g(d["bienestar_alimentos_detalle"], ALIM_COD)


def proyeccion(d26, hist):
    """Saldo PIM-devengado vs ritmo de gasto, para alimentos (comedor) y para Bienestar total."""
    out = {}
    corte_mes = 11  # noviembre en curso (corte 16-nov): meses cerrados = ene-oct
    for key, base, mes in [("alimentos_bienestar", alim_bien(d26), d26["bienestar_alimentos_mes"]),
                           ("bienestar_total", bien(d26), d26["bienestar_mes"])]:
        if not base:
            continue
        dev_ene_oct = sum(m["dev"] for m in mes if m["mes"] <= 10)
        prom = dev_ene_oct / 10
        meses_activos = [m for m in mes if m["mes"] <= 10 and m["dev"] > 0]
        prom_activos = dev_ene_oct / len(meses_activos) if meses_activos else 0
        saldo_pim = base["pim"] - base["dev"]
        saldo_cert = base["cert"] - base["dev"]
        comp_por_devengar = base["comp_anual"] - base["dev"]
        sin_certificar = base["pim"] - base["cert"]
        # meses restantes nov(16->30) + dic ~= 1.5 meses
        necesidad = prom * 1.5
        # patrón histórico: % del devengado anual que se registró en nov+dic
        pat = []
        for y, dy in sorted(hist.items()):
            if y == 2026:
                continue
            mm = dy["bienestar_alimentos_mes"] if key == "alimentos_bienestar" else dy["bienestar_mes"]
            tot = sum(m["dev"] for m in mm)
            nd = sum(m["dev"] for m in mm if m["mes"] >= 11)
            if tot > 0:
                pat.append({"year": y, "dev_anual": round(tot, 2), "dev_nov_dic": round(nd, 2),
                            "pct_nov_dic": round(100 * nd / tot, 1)})
        # Rango de necesidad nov+dic según el patrón histórico (% del devengado anual en nov+dic):
        # dev_ene_oct * p / (100 - p) con p = mín y máx del patrón. También ritmo de meses con gasto * 1.5.
        pcts = [x["pct_nov_dic"] for x in pat if x["pct_nov_dic"] < 100]
        rango = ([round(dev_ene_oct * min(pcts) / (100 - min(pcts)), 2), round(dev_ene_oct * max(pcts) / (100 - max(pcts)), 2)]
                 if pcts else None)
        nec_activos = prom_activos * 1.5
        if rango:
            alcanza = True if saldo_pim >= rango[1] else (False if saldo_pim < rango[0] else "incierto")
        else:
            alcanza = "incierto"
        out[key] = {
            "pim": base["pim"], "certificado": base["cert"], "compromiso_anual": base["comp_anual"],
            "devengado": base["dev"], "avance_pct": base.get("avance_pct"),
            "devengado_ene_oct": round(dev_ene_oct, 2),
            "promedio_mensual_ene_oct": round(prom, 2),
            "promedio_mensual_meses_con_gasto": round(prom_activos, 2),
            "saldo_pim_menos_devengado": round(saldo_pim, 2),
            "saldo_certificado_por_devengar": round(saldo_cert, 2),
            "compromisos_por_devengar": round(comp_por_devengar, 2),
            "pim_sin_certificar": round(sin_certificar, 2),
            "necesidad_estimada_16nov_31dic": round(necesidad, 2),
            "meses_cubiertos_por_saldo_pim": round(saldo_pim / prom, 1) if prom else None,
            "necesidad_nov_dic_rango_patron_historico": rango,
            "necesidad_ritmo_meses_con_gasto_x1_5": round(nec_activos, 2),
            "alcanza_a_diciembre_en_papel": alcanza,
            "alcanza_nota": ("true solo si el saldo PIM cubre el máximo del rango histórico nov+dic; false si no cubre ni el mínimo; "
                             "'incierto' en los demás casos. necesidad_estimada_16nov_31dic (promedio ene-oct x 1.5) subestima: "
                             "el contrato empezó en abril y nov+dic concentran históricamente un tercio o más del devengado anual."),
            "patron_historico_nov_dic": pat,
        }
    return out


NOTICIAS = []  # sin noticias verificadas sobre el comedor UNALM al corte; agregar solo con URL y fuente


def main():
    lo = int(sys.argv[1]) if len(sys.argv) > 1 else 2018
    hi = int(sys.argv[2]) if len(sys.argv) > 2 else 2026
    cache = json.load(open(OUT)).get("detalle_anual", {}) if os.path.exists(OUT) else {}
    for y in range(lo, hi + 1):
        if str(y) in cache and (y != 2026 or os.environ.get("CACHE_ONLY") == "1") and os.environ.get("REFRESH") != "1":
            print(f"[{y}] cache", flush=True)
            continue
        print(f"[{y}] scraping…", flush=True)
        cache[str(y)] = scrape_year(y, full=(y == 2026))
        # guardado incremental (el JSON de salida es la caché): si algo falla después no se pierde el scraping
        prev = json.load(open(OUT)) if os.path.exists(OUT) else {}
        prev["detalle_anual"] = cache
        json.dump(prev, open(OUT, "w"), ensure_ascii=False, indent=1)
        b = bien(cache[str(y)]); a = alim_bien(cache[str(y)])
        print(f"   Bienestar PIM {b and b['pim']} dev {b and b['dev']} | servicio alimentación PIM {a and a['pim']} dev {a and a['dev']}", flush=True)

    hist = {int(k): v for k, v in cache.items()}
    serie = []
    for y in sorted(hist):
        d = hist[y]
        b, a = bien(d), alim_bien(d)
        pa = g(d["pliego_alimentos_detalle"], ALIM_COD)
        p = d["producto_apoyo_estudiante"]
        serie.append({
            "year": y,
            "comedor_alimentos": {k: a.get(k) for k in ("pia", "pim", "cert", "comp_anual", "dev", "gir", "avance_pct")} if a else None,
            "bienestar_asistencia_social": {k: b.get(k) for k in ("pia", "pim", "cert", "comp_anual", "dev", "gir", "avance_pct")} if b else None,
            "producto_apoyo_estudiante": {k: p.get(k) for k in ("pia", "pim", "cert", "dev", "avance_pct")} if p else None,
            "alimentos_consumo_humano_todo_pliego": {k: pa.get(k) for k in ("pia", "pim", "cert", "dev", "avance_pct")} if pa else None,
            "parcial": y == 2026,
        })
    d26 = hist.get(2026)
    noticias = NOTICIAS
    out = {
        "_meta": {
            "titulo": "Presupuesto del comedor universitario y bienestar estudiantil — UNALM (pliego 518)",
            "fuente": "Consulta Amigable MEF (apps5.mineco.gob.pe/transparencia/Navegador/), modo Actividades y Proyectos; "
                      "Gobierno Nacional > Sector 10 Educación > Pliego 518 U.N. Agraria La Molina.",
            "url": "https://apps5.mineco.gob.pe/transparencia/Navegador/default.aspx",
            "corte": CORTE,
            "extraido": datetime.date.today().isoformat(),
            "unidad": "S/ corrientes",
            "definiciones": {
                "comedor_alimentos": "Específica 2.3.2.7.11.5 'Servicios de alimentación de consumo humano' dentro de la actividad 5005863 "
                                     "BIENESTAR Y ASISTENCIA SOCIAL (producto 3000786 SERVICIOS ADECUADOS DE APOYO AL ESTUDIANTE, PP 0066). "
                                     "La UNALM terceriza el comedor (contrato de servicio de alimentación con concesionario), por eso se usa esta "
                                     "específica de servicios y no la de víveres (2.3.1.1.1.1, casi nula en Bienestar: PIM 2026 S/ 1,114). "
                                     "Es la mejor aproximación pública al pago del servicio de comedor.",
                "bienestar_asistencia_social": "Actividad 5005863 completa (incluye comedor, pero también salud, servicio social, "
                                               "deporte/cultura, personal CAS, etc.).",
                "columnas": "pia=Presupuesto Inicial de Apertura, pim=Presupuesto Institucional Modificado, cert=certificado, "
                            "comp_anual=compromiso anual, dev=devengado (gasto reconocido), gir=girado (pagado).",
            },
            "caveats": [
                "La Consulta Amigable no muestra el nombre de la META presupuestal; 'comedor' se infiere por actividad (Bienestar) + "
                "específica (2.3.2.7.11.5 servicios de alimentación de consumo humano). Gastos del comedor en otras específicas (equipos, "
                "mantenimiento de ambientes, inspección sanitaria, personal) quedan dentro de 'bienestar_asistencia_social' pero mezclados con otros servicios de bienestar.",
                "Hay servicios de alimentación también en otras actividades (Gestión Administrativa, Unidades de Enseñanza y "
                "Producción, etc.) que NO se cuentan como comedor (refrigerios, eventos, etc.); ver alimentos_consumo_humano_todo_pliego (misma específica en todo el pliego).",
                "Devengado = gasto reconocido contablemente (pago al concesionario por raciones conformadas), no raciones servidas; "
                "el servicio suele devengarse con rezago mensual, así que un mes sin devengado no implica que no se haya servido comida.",
                "2026 es parcial (corte 16-nov-2026). Noviembre está incompleto. La proyección es aritmética simple (promedio ene-oct), "
                "no considera calendario académico, vacaciones, ni contratos ya comprometidos con entregas programadas.",
                "2018-2020: la específica 2.3.2.7.11.5 no aparece en la actividad Bienestar (comedor_alimentos = null), aunque en esos "
                "años había contrato de concesionario (CP-SM-1-2018/2019, S/ 2.31 M c/u, ver comedor-contratos-unalm.json). El pago "
                "pudo registrarse en otra específica/actividad o el concesionario cobrar directamente; no se infiere un monto. "
                "Usar bienestar_asistencia_social como referencia para esos años.",
                "La API SQL de Datos Abiertos MEF devolvió 404 el 16-nov-2026; no se pudieron obtener nombres de meta ni detalle por "
                "META_NOMBRE. Reintentar para confirmar la meta del comedor.",
            ],
        },
        "serie": serie,
        "detalle_anual": hist,
        "proyeccion_2026": proyeccion(d26, hist) if d26 else None,
        "noticias": noticias,
    }
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)
    print("OK ->", os.path.abspath(OUT))
    if d26:
        print(json.dumps(out["proyeccion_2026"], ensure_ascii=False, indent=1)[:3000])


if __name__ == "__main__":
    main()
