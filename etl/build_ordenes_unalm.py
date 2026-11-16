#!/usr/bin/env python3
"""Órdenes de compra y de servicio de la UNALM (RUC 20147897406) desde CONOSCE (OECE).

Fuente: CONOSCE · Datos Abiertos OECE, dataset "Órdenes de compra y servicio" (incluye
las contrataciones hasta 8 UIT, que no pasan por procedimiento de selección).
  https://conosce.osce.gob.pe/buscador/assets/67ae6c4a/reportes/ordenes/<AAAA>/ORDENESCOMPRA<MES><AAAA>_0.xlsx
  (MES en mayúsculas; setiembre = "SEPTIEMBRE"; ya NO lleva el prefijo "CONOSCE_",
   que hoy devuelve 404). Enlace oficial: portal Pentaho
   https://bi.seace.gob.pe/pentaho/api/repos/:public:portal:datosabiertos.html/content?userid=public&password=key
   → js/renderhtmlv2.js → generateOrdenesDataset().

Cada XLSX mensual (~20 MB, ~150k filas, todo el Estado) se descarga a un tmp, se filtra
RUC_ENTIDAD == 20147897406 con un lector XLSX en streaming (solo stdlib) y se guarda
SOLO el subconjunto UNALM (filas crudas + cabecera + Last-Modified) en
etl/cache_ordenes/AAAA-MM.json. El XLSX completo se borra.

Uso:
  python3 etl/build_ordenes_unalm.py                 # 2024-01 .. mes actual (usa caché)
  python3 etl/build_ordenes_unalm.py --refresh 2026  # re-descarga meses que empiecen por '2026'
  python3 etl/build_ordenes_unalm.py --desde 2023-01

Salida: etl/work/ordenes-unalm.json (fuera de data/: contiene PII, no se publica)
Requiere IP peruana (OECE geobloquea datacenters).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(HERE, "cache_ordenes")
OUT = os.path.join(ROOT, "etl", "work", "ordenes-unalm.json")  # NO publicar: trae nombres/RUC de personas naturales (p. ej. tesistas)

RUC_ENT = "20147897406"
BASE = "https://conosce.osce.gob.pe/buscador/assets/67ae6c4a/reportes/ordenes"
MESES = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO",
         "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# ---------------------------------------------------------------- descarga + filtro

def url_mes(y: int, m: int) -> str:
    return f"{BASE}/{y}/ORDENESCOMPRA{MESES[m-1]}{y}_0.xlsx"


def _col(ref: str) -> int:
    n = 0
    for ch in ref:
        if ch.isalpha():
            n = n * 26 + ord(ch) - 64
        else:
            break
    return n - 1


def filtrar_xlsx(path: str):
    """Devuelve (cabecera, filas UNALM, total_filas) leyendo el XLSX en streaming."""
    z = zipfile.ZipFile(path)
    ss = []
    for _, el in ET.iterparse(z.open("xl/sharedStrings.xml")):
        if el.tag == NS + "si":
            ss.append("".join(t.text or "" for t in el.iter(NS + "t")))
            el.clear()
    hdr, hits, n = None, [], 0
    for _, el in ET.iterparse(z.open("xl/worksheets/sheet1.xml")):
        if el.tag != NS + "row":
            continue
        vals = {}
        for c in el.iter(NS + "c"):
            v = c.find(NS + "v")
            if v is None:
                isel = c.find(NS + "is")
                val = "".join(x.text or "" for x in isel.iter(NS + "t")) if isel is not None else None
            else:
                val = ss[int(v.text)] if c.get("t") == "s" else v.text
            vals[_col(c.get("r"))] = val
        el.clear()
        row = [vals.get(i) for i in range(max(vals) + 1)] if vals else []
        if hdr is None:
            hdr = row
            idx = hdr.index("RUC_ENTIDAD")
            continue
        n += 1
        if len(row) > idx and (row[idx] or "").strip() == RUC_ENT:
            hits.append(row)
    return hdr, hits, n


def bajar_mes(y: int, m: int, refresh: bool) -> str:
    key = f"{y}-{m:02d}"
    dest = os.path.join(CACHE, key + ".json")
    if os.path.exists(dest) and not refresh:
        return f"{key}: caché"
    url = url_mes(y, m)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        resp = urllib.request.urlopen(req, timeout=300)
    except urllib.error.HTTPError as e:
        return f"{key}: HTTP {e.code} (no publicado)"
    lastmod = resp.headers.get("Last-Modified")
    fd, tmp = tempfile.mkstemp(suffix=".xlsx")
    try:
        with os.fdopen(fd, "wb") as f:
            while True:
                b = resp.read(1 << 20)
                if not b:
                    break
                f.write(b)
        hdr, hits, n = filtrar_xlsx(tmp)
    finally:
        os.remove(tmp)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump({"url": url, "last_modified": lastmod, "descargado": dt.datetime.now().isoformat(timespec="seconds"),
                   "filas_archivo": n, "header": hdr, "rows": hits}, f, ensure_ascii=False)
    return f"{key}: {len(hits)} filas UNALM de {n}"

# ---------------------------------------------------------------- clasificación comedor

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s.upper())

# UNALM: el comedor es un servicio tercerizado (concesionario) a cargo de la unidad orgánica 02.900.07.00, que aparece
# en la descripción de las órdenes ("SERVICIO DE COMEDOR UNIVERSITARIO (02.900.07.00)", "SUPERVISION SANITARIA DEL
# COMEDOR (02.900.07.00)", "EQUIPAMIENTO DE COMEDOR UNIV. U.OP:02.900.07.00", marmita, caldero, peladora de papas...).
RE_COMEDOR = re.compile(r"COMEDOR|SERVICIOS? ALIMENTICIOS|\bRACION(ES)?\b|ALIMENTACION (DE|PARA|A) (LOS )?(ESTUDIANTES|ALUMNOS|COMENSALES|BECARIOS)|\bCOMENSAL")
RE_UO_COMEDOR = re.compile(r"0?2 ?\. ?900 ?\. ?07")
# Eventos/catering: no son el comedor aunque digan "servicio de alimentación".
RE_EVENTO = re.compile(r"CATERING|REFRIGERIO|COFFEE BREAK|BOCADITOS|AGASAJO|BUFFET")
RE_CAT = [
    ("servicio_alimentacion", re.compile(r"SERVICIO DE COMEDOR|CONCESION|SERVICIO DE ALIMENTACION|\\bRACION")),
    ("inspeccion_sanitaria", re.compile(r"SUPERVISION SANITARIA|INSPECCION|SUPERVISIONES|MONITOREO|NUTRICION")),
    ("infraestructura", re.compile(r"CONSTRUCCION|\bOBRA\b|VALORIZACION|EXPEDIENTE TECNICO|PINTADO|FACHADA|PUERTAS|VENTANAS|REJAS|SERVICIOS? (DE )?HIGIENICOS|MAMPARA|MANPARA|GASFITERIA|CEMENTO|YESO|TANQUE|DESAGUE|AGUA POTABLE")),
    ("mantenimiento_equipos", re.compile(r"MANTENIMIENTO|MANTENIMEINTO|MANT\b|ASCENSOR|MONTACARGA|REPUESTO|REPARACION|CALDER|MARMITA|RED DE GAS|RESISTENCIA|EXTINTOR")),
    ("equipamiento", re.compile(r"EQUIPAMIENTO|EQUIPO|PELADORA|COCHE METALICO|VITRINA|LAVATORIO|REJILLA|BANCAS|TABLERO|LUMINARIA|ELECTRIC|CAMARA|BOMBA")),
    ("operacion_enseres_limpieza", re.compile(r"ENSERES|ASEO|LIMPIEZA|MENAJE|UTENSILIO|UTENCILIO|VAJILLA|TACHOS|VESTUARIO|PRENDAS|HERRAMIENTA|MATERIALES|UTILES|STOCKA")),
    ("alimentos", re.compile(r"ALIMENTO|VIVERES|ABARROTES|CARNE|VERDURA|FRUTA|\bPAN\b|HUEVO|\bGAS\b|GLP")),
]


def clasificar(desc: str):
    """-> (confianza, palabra_clave, categoria) o (None, None, None).
    explicito = la descripción menciona el comedor / servicios alimenticios;
    probable  = solo lleva el código de la unidad orgánica del comedor (02.900.07.00), sin mencionarlo.
    No se marcan víveres sueltos: en la UNALM son de la panadería/centro de producción (U.O. 03.719), piscigranja
    (truchas), eventos o atención a visitas, no del comedor (que está tercerizado)."""
    d = norm(desc)
    m = RE_COMEDOR.search(d)
    if m and not RE_EVENTO.search(d):
        cat = next((c for c, rx in RE_CAT if rx.search(d)), "sin_detalle")
        return "explicito", m.group(0), cat
    m = RE_UO_COMEDOR.search(d)
    if m:
        cat = next((c for c, rx in RE_CAT if rx.search(d)), "otros_unidad_comedor")
        return "probable", "U.O. 02.900.07.00", cat
    return None, None, None


# ---------------------------------------------------------------- armado

def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def fecha(x):
    x = (x or "").strip()[:10]
    return x if re.match(r"\d{4}-\d{2}-\d{2}$", x) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", default="2024-01")
    ap.add_argument("--hasta", default=dt.date.today().strftime("%Y-%m"))
    ap.add_argument("--refresh", default="", help="prefijo AAAA o AAAA-MM de meses a re-descargar")
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    os.makedirs(CACHE, exist_ok=True)

    y0, m0 = map(int, a.desde.split("-"))
    y1, m1 = map(int, a.hasta.split("-"))
    meses = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        meses.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    refresh = lambda k: bool(a.refresh) and k.startswith(a.refresh)
    with ThreadPoolExecutor(a.workers) as ex:
        for r in ex.map(lambda ym: bajar_mes(ym[0], ym[1], refresh(f"{ym[0]}-{ym[1]:02d}")), meses):
            print(r, flush=True)

    # --- consolidar, dedupe por ORDEN (texto único por entidad/año) quedándose con el archivo más reciente
    archivos, ordenes = [], {}
    for (y, m) in meses:
        p = os.path.join(CACHE, f"{y}-{m:02d}.json")
        if not os.path.exists(p):
            archivos.append({"mes": f"{y}-{m:02d}", "estado": "no publicado / no descargado"})
            continue
        c = json.load(open(p, encoding="utf-8"))
        archivos.append({"mes": f"{y}-{m:02d}", "url": c["url"], "last_modified": c["last_modified"],
                         "filas_unalm": len(c["rows"]), "filas_archivo": c["filas_archivo"]})
        H = {h: i for i, h in enumerate(c["header"])}
        g = lambda r, k: (r[H[k]] if H[k] < len(r) else None)
        for r in c["rows"]:
            desc = (g(r, "DESCRIPCION_ORDEN") or "").strip()
            fe = fecha(g(r, "FECHA_DE_EMISION")) or fecha(g(r, "FECHA_COMPROMISO_PRESUPUESTAL")) or fecha(g(r, "FECHA_REGISTRO"))
            tipo = g(r, "TIPOORDEN") or ""
            cl, kw, cat = clasificar(desc)
            o = {
                "fecha": fe,
                "fecha_registro": fecha(g(r, "FECHA_REGISTRO")),
                "nro": (g(r, "NRO_DE_ORDEN") or "").strip(),
                "orden": (g(r, "ORDEN") or "").strip(),
                "tipo": "compra" if "Compra" in tipo else ("servicio" if "Servicio" in tipo else tipo),
                "descripcion": desc,
                "objeto": g(r, "OBJETOCONTRACTUAL"),
                "estado": g(r, "ESTADOCONTRATACION"),
                "tipo_contratacion": g(r, "TIPODECONTRATACION"),
                "monto": round(fnum(g(r, "MONTO_TOTAL_ORDEN_ORIGINAL")), 2),
                "moneda": g(r, "MONEDA"),
                "proveedor": re.sub(r"\s+", " ", (g(r, "NOMBRE_RAZON_CONTRATISTA") or "")).strip(),
                "ruc": (g(r, "RUC_CONTRATISTA") or "").strip(),
                "comedor": cl is not None,
                "comedor_conf": cl,
                "comedor_kw": kw,
                "comedor_cat": cat,
                "archivo_mes": f"{y}-{m:02d}",
            }
            k = (o["orden"] or f"{o['tipo']}-{o['nro']}", o["ruc"], o["fecha"])
            ordenes[k] = o  # meses posteriores pisan (versión más reciente)
    L = sorted(ordenes.values(), key=lambda o: (o["fecha"] or "", o["orden"]))

    vigente = lambda o: "ANUL" not in norm(o["estado"] or "")
    pen = lambda o: "SOLES" in norm(o["moneda"] or "")
    V = [o for o in L if vigente(o)]
    no_pen = [o for o in V if not pen(o)]

    def agg(rows, keyf):
        d = defaultdict(lambda: {"n": 0, "monto": 0.0, "n_compra": 0, "n_servicio": 0, "monto_compra": 0.0, "monto_servicio": 0.0})
        for o in rows:
            if not pen(o):
                continue
            k = keyf(o)
            if k is None:
                continue
            x = d[k]
            x["n"] += 1
            x["monto"] += o["monto"]
            t = o["tipo"] if o["tipo"] in ("compra", "servicio") else None
            if t:
                x["n_" + t] += 1
                x["monto_" + t] += o["monto"]
        return {k: {kk: (round(vv, 2) if isinstance(vv, float) else vv) for kk, vv in v.items()} for k, v in sorted(d.items())}

    anio = lambda o: (o["fecha"] or "")[:4] or None
    mes = lambda o: (o["fecha"] or "")[:7] or None
    menor8 = lambda o: "8 UIT" in (o["tipo_contratacion"] or "")
    C = [o for o in V if o["comedor"]]

    def top(rows, n=40):
        d = defaultdict(lambda: {"monto": 0.0, "n": 0, "nombre": None, "desde": None, "hasta": None})
        for o in rows:
            if not pen(o):
                continue
            x = d[o["ruc"]]
            x["monto"] += o["monto"]
            x["n"] += 1
            x["nombre"] = x["nombre"] or o["proveedor"]
            f = o["fecha"]
            if f:
                x["desde"] = min(x["desde"] or f, f)
                x["hasta"] = max(x["hasta"] or f, f)
        out = [{"ruc": k, "nombre": v["nombre"], "tipo_persona": "natural" if k.startswith(("10", "15")) else "juridica",
                "monto": round(v["monto"], 2), "n": v["n"], "desde": v["desde"], "hasta": v["hasta"]} for k, v in d.items()]
        return sorted(out, key=lambda r: -r["monto"])[:n]

    fechas = [o["fecha"] for o in V if o["fecha"]]
    estados = defaultdict(int)
    tipos_contr = defaultdict(int)
    for o in L:
        estados[o["estado"]] += 1
        tipos_contr[o["tipo_contratacion"]] += 1

    res = {
        "_meta": {
            "titulo": "Órdenes de compra y de servicio de la UNALM",
            "entidad": "Universidad Nacional Agraria La Molina (UNALM)",
            "ruc_entidad": RUC_ENT,
            "fuente": "OECE - CONOSCE Datos Abiertos, dataset 'Órdenes de compra y servicio' (archivos mensuales ORDENESCOMPRA<MES><AAAA>_0.xlsx)",
            "fuente_url": BASE + "/",
            "portal": "https://bi.seace.gob.pe/pentaho/api/repos/:public:portal:datosabiertos.html/content?userid=public&password=key",
            "periodo_solicitado": f"{a.desde} a {a.hasta}",
            "fecha_min": min(fechas) if fechas else None,
            "fecha_max": max(fechas) if fechas else None,
            "generado": dt.datetime.now().isoformat(timespec="seconds"),
            "archivos": archivos,
            "unidad_monto": "Soles (PEN), MONTO_TOTAL_ORDEN_ORIGINAL (monto al emitir la orden; no incluye ampliaciones/anulaciones parciales ni lo efectivamente pagado/devengado)",
            "fecha_usada": "FECHA_DE_EMISION de la orden (si falta: compromiso presupuestal, luego registro). Cada archivo mensual de CONOSCE agrupa por mes de emisión e incluye órdenes registradas en SEACE semanas o meses después (FECHA_REGISTRO).",
            "estados": dict(estados),
            "tipos_contratacion": dict(tipos_contr),
            "criterio_comedor": ("Clasificación automática por texto de DESCRIPCION_ORDEN (regex, sin IA). "
                                 "'explicito' = menciona COMEDOR, SERVICIOS ALIMENTICIOS, RACIONES o 'alimentación para estudiantes'. "
                                 "'probable' = no menciona el comedor pero se imputa a la unidad orgánica 02.900.07.00, la misma del "
                                 "'SERVICIO DE COMEDOR UNIVERSITARIO (02.900.07.00)' y de la 'SUPERVISION SANITARIA DEL COMEDOR (02.900.07.00)' "
                                 "(mantenimiento de marmita/caldero/ascensores/montacargas, utensilios, limpieza, reparaciones del local). "
                                 "Que esa unidad sea el comedor es una INFERENCIA a partir de esas órdenes; no se encontró el catálogo de "
                                 "unidades orgánicas de la UNALM. "
                                 "comedor_cat: servicio_alimentacion (pago al concesionario), inspeccion_sanitaria, infraestructura, "
                                 "mantenimiento_equipos, equipamiento, operacion_enseres_limpieza, alimentos, sin_detalle, otros_unidad_comedor. "
                                 "NO se marcan víveres sueltos (azúcar, huevos, verduras, harina): en la UNALM son de la panadería/centro "
                                 "de producción (U.O. 03.719.00.00, con 'ventas al público'), la piscigranja (truchas) o atención a visitas. "
                                 "Se excluyen catering/refrigerios de eventos."),
            "caveats": [
                "CONOSCE solo incluye órdenes registradas en el SEACE; las órdenes que la entidad no registra (o registra tarde) no aparecen. El mes en curso y el anterior están incompletos: el SEACE se alimenta con rezago (FECHA_REGISTRO suele ser semanas después de la emisión).",
                "Cobertura: la UNALM casi no tiene órdenes en CONOSCE antes de julio de 2024 (ene-jun 2024: 3 órdenes); los datos útiles empiezan en jul-2024. No interpretar ese vacío como ausencia de compras.",
                "Cobertura: desde junio de 2025 hasta noviembre de 2026 los archivos mensuales de CONOSCE NO traen ninguna orden de la UNALM (RUC 20147897406), aunque sí de otras entidades (~115-380 mil filas/mes); abril 2025 también sale vacío. Verificado buscando el RUC y el nombre de la entidad en el archivo de octubre 2025. Coincide con la entrada en vigor de la nueva Ley General de Contrataciones Públicas (Ley 32069, 22-abr-2025), pero la causa no está documentada: NO significa que la UNALM no haya emitido órdenes. Órdenes de jun-2025 en adelante: no disponibles en esta fuente.",
                "Incluye órdenes de todos los tipos de contratación (≤8 UIT, derivadas de procedimientos, catálogo electrónico, etc.); ver campo tipo_contratacion.",
                "Totales y rankings excluyen órdenes en estado anulado y montos en moneda distinta a soles.",
                "La marca 'comedor' es heurística: no prueba destino final; revise la descripción. Puede haber compras del comedor con descripciones genéricas que no se detectan.",
                "Personas naturales (RUC 10) son locadores/proveedores del Estado; se listan con su nombre tal como figura en SEACE.",
                "Nombres con '¿' provienen de caracteres (Ñ, tildes) mal codificados en la fuente.",
            ],
        },
        "totales": {
            "n_ordenes": len(L),
            "n_vigentes": len(V),
            "n_anuladas": len(L) - len(V),
            "n_moneda_extranjera": len(no_pen),
            "monto_vigente_pen": round(sum(o["monto"] for o in V if pen(o)), 2),
            "monto_menor_8uit_pen": round(sum(o["monto"] for o in V if pen(o) and menor8(o)), 2),
            "n_menor_8uit": sum(1 for o in V if menor8(o)),
            "por_anio": agg(V, anio),
            "por_mes": agg(V, mes),
        },
        "comedor": {
            "n": len(C),
            "n_explicito": sum(1 for o in C if o["comedor_conf"] == "explicito"),
            "monto_pen": round(sum(o["monto"] for o in C if pen(o)), 2),
            "por_anio": agg(C, anio),
            "por_mes": agg(C, mes),
            "explicito_por_mes": agg([o for o in C if o["comedor_conf"] == "explicito"], mes),
            "por_categoria": agg(C, lambda o: f'{o["comedor_conf"]}:{o["comedor_cat"]}'),
            "alimentos_explicito_por_mes": agg([o for o in C if o["comedor_conf"] == "explicito" and o["comedor_cat"] == "alimentos"], mes),
            "por_mes_categoria": agg([o for o in C if o["comedor_conf"] == "explicito"], lambda o: f'{(o["fecha"] or "")[:7]}|{o["comedor_cat"]}'),
            "top_proveedores": top(C, 30),
            "ultima_orden": max((o["fecha"] for o in C if o["fecha"]), default=None),
        },
        "top_proveedores": top(V, 50),
        "ordenes": L,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, separators=(",", ":"))
    print(f"OK {OUT}: {len(L)} órdenes ({len(V)} vigentes), comedor={len(C)}, {res['_meta']['fecha_min']}..{res['_meta']['fecha_max']}")


if __name__ == "__main__":
    sys.exit(main())
