#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Consulta Amigable MEF -> presupuesto del pliego 518 (U.N. AGRARIA LA MOLINA).

Drill: Año -> Nivel de Gobierno -> E: GOBIERNO NACIONAL -> Sector 10 EDUCACION -> Pliego 518.
 - Serie: fila del pliego 518 por año (PIA/PIM/Certificado/Compromiso anual/
   Atención compromiso mensual/Devengado/Girado/Avance%).
 - Detalle (años en DETALLE_YEARS): desde el nivel pliego se pulsa cada botón de
   desagregación (Ejecutora, Función, Genérica, Producto/Proyecto, Fuente,
   Categoría Presupuestal) y se parsean las filas.
Guarda raw en etl/ca_unalm_raw.json. El JSON del dashboard lo arma merge_presupuesto.py.

Uso (venv fuera del repo):
  ~/.venvs/observatorio-unalm/bin/python etl/scraper_ca_unalm.py [anio_ini] [anio_fin]
Adaptado del scraper UNSAAC (pliego 511) / San Marcos (pliego 510).
"""
import requests, re, json, os, time, sys, datetime
from bs4 import BeautifulSoup

BASE = "https://apps5.mineco.gob.pe/transparencia/Navegador/"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "ca_unalm_raw.json")
PLIEGO = "518"
SECTOR = "10"
DETALLE_YEARS = (2025, 2026)

# clave raw -> (nombre del botón, valor del botón)
BREAKDOWNS = {
    "por_unidad": ("ctl00$CPH1$BtnEjecutora", "Ejecutora"),
    "por_funcion": ("ctl00$CPH1$BtnFuncion", "Función"),
    "por_generica": ("ctl00$CPH1$BtnGenerica", "Genérica"),
    "por_programa": ("ctl00$CPH1$BtnProdProy", "Producto/Proyecto"),
    "por_fuente": ("ctl00$CPH1$BtnFuenteAgregada", "Fuente"),
    "por_cat_presupuestal": ("ctl00$CPH1$BtnProgramaPpto", "Categoría Presupuestal"),
}

LABEL_RE = re.compile(r"^[\w\-\.]+:\s")
NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")


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


def num(s):
    s = (s or "").replace(",", "").strip()
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def table_rows(soup):
    """Filas de resultados: (etiqueta 'COD: NOMBRE', valor grp1 o None, [nums])."""
    out = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        cells = [td.get_text(" ", strip=True) for td in tds]
        idx = next((i for i, c in enumerate(cells) if LABEL_RE.match(c)), None)
        if idx is None:
            continue
        nums = [num(c) for c in cells[idx + 1:] if NUM_RE.match(c.replace(",", ""))]
        nums = [n for n in nums if n is not None]
        if len(nums) < 7:
            continue
        radio = tr.find("input", {"name": "grp1"})
        out.append((cells[idx], radio.get("value") if radio else None, nums))
    return out


def post(s, soup, click_name, click_val, grp1_val, year):
    action = soup.find("form").get("action")
    d = fields(soup)
    if grp1_val is not None:
        d["grp1"] = grp1_val
    d[click_name] = click_val
    r = s.post(BASE + action, data=d,
               headers={"Referer": f"{BASE}Navegar.aspx?y={year}&ap=ActProy"}, timeout=90)
    return BeautifulSoup(r.text, "lxml")


def find(rows, code):
    pat = re.compile(r"^0*%s\s*:" % re.escape(code))
    return next((r for r in rows if pat.match(r[0].strip())), None)


def goto_pliego(s, year):
    """Devuelve (soup a nivel lista de pliegos del sector 10, fila del pliego 518)."""
    s.get(f"{BASE}default.aspx?y={year}&ap=ActProy", timeout=40)
    soup = BeautifulSoup(s.get(f"{BASE}Navegar.aspx?y={year}&ap=ActProy", timeout=40).text, "lxml")
    sel = soup.select_one("#ctl00_CPH1_DrpYear option[selected]")
    if not sel or sel.get_text(strip=True) != str(year):
        raise RuntimeError(f"año no fijado ({sel.get_text(strip=True) if sel else '?'})")
    g = soup.select_one("input[name=grp1]")
    soup = post(s, soup, "ctl00$CPH1$BtnTipoGobierno", "Nivel de Gobierno", g.get("value") if g else None, year)
    e = next((r for r in table_rows(soup) if r[0].strip().upper().startswith("E:")), None)
    if not e:
        raise RuntimeError("no encontré E: GOBIERNO NACIONAL")
    soup = post(s, soup, "ctl00$CPH1$BtnSector", "Sector", e[1], year)
    sec = find(table_rows(soup), SECTOR)
    if not sec:
        raise RuntimeError("no encontré sector 10 EDUCACION")
    soup = post(s, soup, "ctl00$CPH1$BtnPliego", "Pliego", sec[1], year)
    row = find(table_rows(soup), PLIEGO)
    if not row:
        raise RuntimeError(f"no encontré fila pliego {PLIEGO}")
    return soup, row


def build_record(year, nums):
    # Columnas CA: PIA, PIM, Certificación, Compromiso Anual, Atención Comp. Mensual,
    # Devengado, Girado, Avance %
    pia, pim, cert = nums[0], nums[1], nums[2]
    if len(nums) >= 8:
        dev, gir = nums[5], nums[6]
    elif len(nums) == 7:
        dev, gir = nums[5], nums[6]
    else:
        dev, gir = nums[-2], nums[-1]
    ejec = round(100 * dev / pim, 1) if pim else 0
    return {"year": year, "pia": pia, "pim": pim, "cert": cert,
            "dev": dev, "gir": gir, "ejec_pct": ejec}


def scrape_detalle(s, year):
    det = {}
    for key, (btn, val) in BREAKDOWNS.items():
        for attempt in range(4):
            try:
                soup, row = goto_pliego(s, year)
                sub = post(s, soup, btn, val, row[1], year)
                rows = table_rows(sub)
                det[key] = [{"nombre": lbl, "pia": n[0], "pim": n[1], "cert": n[2],
                             "dev": n[5], "gir": n[6]} for lbl, _, n in rows]
                print(f"   [{year}] {key}: {len(rows)} filas", flush=True)
                break
            except Exception as e:
                print(f"   [{year}] {key} intento {attempt}: {repr(e)[:80]}", flush=True)
                time.sleep(6)
                s = new_session()
        time.sleep(2)
    return det, s


def main():
    lo = int(sys.argv[1]) if len(sys.argv) > 1 else 2012
    hi = int(sys.argv[2]) if len(sys.argv) > 2 else 2026
    raw = json.load(open(RAW, encoding="utf-8")) if os.path.exists(RAW) else {}
    raw.setdefault("serie", {}); raw.setdefault("detalle", {})
    s = new_session()
    for y in range(lo, hi + 1):
        for attempt in range(4):
            try:
                _, (label, _, nums) = goto_pliego(s, y)
                rec = build_record(y, nums)
                rec["label"] = label
                rec["consultado"] = datetime.date.today().isoformat()
                raw["serie"][str(y)] = rec
                print(f"[{y}] {label} -> PIA {rec['pia']/1e6:.1f}M PIM {rec['pim']/1e6:.1f}M "
                      f"Dev {rec['dev']/1e6:.1f}M ({rec['ejec_pct']}%)", flush=True)
                break
            except Exception as e:
                print(f"[{y}] intento {attempt}: {repr(e)[:80]}", flush=True)
                time.sleep(6)
                s = new_session()
        time.sleep(2)
        if y in DETALLE_YEARS:
            det, s = scrape_detalle(s, y)
            det["consultado"] = datetime.date.today().isoformat()
            raw["detalle"][str(y)] = det
        json.dump(raw, open(RAW, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print("\nAños serie:", sorted(raw["serie"].keys()), flush=True)


if __name__ == "__main__":
    main()
