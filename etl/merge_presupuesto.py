#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Arma data/presupuesto-unalm.json a partir de etl/ca_unalm_raw.json
(salida de scraper_ca_unalm.py, Consulta Amigable MEF, pliego 518).

Estructura idéntica a observatorio-unsaac/data/presupuesto-unsaac.json:
  _meta{fuente,pliego,unidad,extraido,nota}
  serie[{year,pia,pim,cert,dev,gir,ejec_pct[,parcial]}]
  detalle_ultimo_anio{anio,por_unidad,por_funcion,por_categoria,por_generica,
                      por_facultad,por_programa}  (cada item {nombre,pim,dev})
Extras (el dashboard los ignora si no los usa): por_fuente, por_cat_presupuestal,
detalle_anio_en_curso, corte.
"""
import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW = os.path.join(HERE, "ca_unalm_raw.json")
TARGET = os.path.join(ROOT, "data", "presupuesto-unalm.json")
CUR_YEAR = 2026
DETALLE_YEAR = 2025  # último año cerrado (como en UNSAAC)

CATEGORIA = {"5": "GASTO CORRIENTE", "6": "GASTO DE CAPITAL", "7": "SERVICIO DE LA DEUDA"}


def strip_code(lbl):
    return re.sub(r"^[\w\-\.]+:\s*", "", lbl).strip()


def items(rows, keep_code=False):
    out = [{"nombre": (r["nombre"] if keep_code else strip_code(r["nombre"])),
            "pim": r["pim"], "dev": r["dev"]} for r in rows if (r["pim"] or 0) > 0]
    return sorted(out, key=lambda x: -x["pim"])


def build_detalle(d, anio):
    if not d:
        return None
    gen = d.get("por_generica", [])
    cat = {}
    for r in gen:
        c = CATEGORIA.get(r["nombre"].strip()[:1], "OTRA")
        x = cat.setdefault(c, {"nombre": c, "pim": 0.0, "dev": 0.0})
        x["pim"] = round(x["pim"] + r["pim"], 2); x["dev"] = round(x["dev"] + r["dev"], 2)
    return {
        "anio": anio,
        "por_unidad": items(d.get("por_unidad", [])),
        "por_funcion": items(d.get("por_funcion", [])),
        "por_categoria": sorted(cat.values(), key=lambda x: -x["pim"]),
        "por_generica": items(gen),
        "por_facultad": [],
        "por_programa": items(d.get("por_programa", [])),
        "por_fuente": items(d.get("por_fuente", [])),
        "por_cat_presupuestal": items(d.get("por_cat_presupuestal", [])),
        "consultado": d.get("consultado"),
    }


def main():
    raw = json.load(open(RAW, encoding="utf-8"))
    serie = []
    for y in sorted(int(k) for k in raw["serie"]):
        r = raw["serie"][str(y)]
        pim, dev = r["pim"], r["dev"]
        rec = {"year": y, "pia": r["pia"], "pim": pim, "cert": r["cert"],
               "dev": dev, "gir": r["gir"],
               "ejec_pct": round(100 * dev / pim, 1) if pim else 0}
        if y == CUR_YEAR:
            rec["parcial"] = True
            rec["corte"] = r.get("consultado")
        serie.append(rec)

    corte = raw["serie"].get(str(CUR_YEAR), {}).get("consultado")
    years = [s["year"] for s in serie]
    det = build_detalle(raw["detalle"].get(str(DETALLE_YEAR)), DETALLE_YEAR)
    det_cur = build_detalle(raw["detalle"].get(str(CUR_YEAR)), CUR_YEAR)
    if det_cur:
        det_cur["parcial"] = True

    doc = {
        "_meta": {
            "fuente": "MEF - Consulta Amigable SIAF (apps5.mineco.gob.pe/transparencia/Navegador)",
            "pliego": "518 · U.N. AGRARIA LA MOLINA (UNALM)",
            "unidad": "S/ (soles corrientes)",
            "extraido": corte,
            "nota": (f"Serie histórica {years[0]}-{years[-1]} vía Consulta Amigable MEF "
                     "(Gobierno Nacional > Sector 10 Educación > pliego 518; una sola unidad "
                     "ejecutora 001-96). Actividades y Proyectos. "
                     f"{DETALLE_YEAR} cerrado; {CUR_YEAR} en ejecución (parcial, corte "
                     f"{corte}). Detalle por programa = Producto/Proyecto SIAF; "
                     "categoría derivada del prefijo de la genérica (5=corriente, 6=capital). "
                     "Desagregado por meta/facultad no disponible en la Consulta Amigable a "
                     "nivel pliego (por_facultad vacío). La API SQL de Datos Abiertos MEF "
                     "respondió 404 el 16-nov-2026, por eso no se usó.") if serie else "sin datos",
            "ejecutoras": [{"codigo": "001-96",
                            "nombre": "UNIVERSIDAD NACIONAL AGRARIA LA MOLINA"}],
        },
        "serie": serie,
        "detalle_ultimo_anio": det or {"anio": None, "por_unidad": [], "por_funcion": [],
                                       "por_categoria": [], "por_generica": [],
                                       "por_facultad": [], "por_programa": [],
                                       "nota": "no disponible"},
    }
    if det_cur:
        doc["detalle_anio_en_curso"] = det_cur

    os.makedirs(os.path.dirname(TARGET), exist_ok=True)
    json.dump(doc, open(TARGET, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("Serie:", years)
    for r in serie:
        p = " PARCIAL" if r.get("parcial") else ""
        print(f"  {r['year']}: PIA {r['pia']/1e6:.1f}M PIM {r['pim']/1e6:.1f}M "
              f"dev {r['dev']/1e6:.1f}M {r['ejec_pct']}%{p}")
    if det:
        tot = sum(x["pim"] for x in det["por_generica"])
        s25 = next((s for s in serie if s["year"] == DETALLE_YEAR), None)
        print(f"Conciliación {DETALLE_YEAR}: suma genéricas PIM {tot:,.2f} vs serie "
              f"{s25['pim'] if s25 else None:,}")


if __name__ == "__main__":
    main()
