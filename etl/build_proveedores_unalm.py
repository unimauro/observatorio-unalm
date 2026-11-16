#!/usr/bin/env python3
"""Enriquece data/proveedores-unalm.json con 'dueno'/'fuente_dueno' (paso 2; correr despues de
build_proveedores_csv.py, que genera la base desde CONOSCE).

1) Consorcios: integrantes segun CONOSCE/OECE - Reporte de Consorcios (CONSORCIO{anio}_0.xlsx,
   columnas AÑO, RUC_CONSORCIO, CONSORCIO, RUC_MIEMBRO, MIEMBRO), cruzando ruc_consorcio = codigo SEACE.
2) Personas juridicas (RUC 20) del top N por monto, e integrantes RUC 20 de esos consorcios:
   gerente general / representante (tabla "Principales ejecutivos") y socios segun RNP-OECE
   tal como los publica datosperu.org (fuente secundaria; cobertura parcial, puede estar desactualizada).
   Las personas naturales (RUC 10/15/17) NO se enriquecen: el proveedor es la propia persona.

Caches (fuera del repo): ~/.cache/observatorio-unalm/{conosce,datosperu}. Los textos de terceros se
tratan como datos no confiables (se extrae texto plano; el frontend debe escapar con esc()).
Uso: ~/.venvs/observatorio-unalm/bin/python etl/build_proveedores_unalm.py [TOP_N]
"""
import html, json, os, re, subprocess, sys, time
import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, '..', 'data', 'proveedores-unalm.json')
CACHE = os.path.expanduser(os.environ.get('UNALM_CACHE', '~/.cache/observatorio-unalm'))
CONS_DIR = os.path.join(CACHE, 'conosce'); DP_DIR = os.path.join(CACHE, 'datosperu')
CONS_URL = 'https://conosce.osce.gob.pe/buscador/assets/67ae6c4a/reportes/consorcios/{y}/CONSORCIO{y}_0.xlsx'
TOP_N = int(sys.argv[1]) if len(sys.argv) > 1 else 40
YEARS = (2023, 2024, 2025, 2026)
MESES = {'ENERO': 1, 'FEBRERO': 2, 'MARZO': 3, 'ABRIL': 4, 'MAYO': 5, 'JUNIO': 6, 'JULIO': 7, 'AGOSTO': 8,
         'SEPTIEMBRE': 9, 'SETIEMBRE': 9, 'OCTUBRE': 10, 'NOVIEMBRE': 11, 'DICIEMBRE': 12}
os.makedirs(CONS_DIR, exist_ok=True); os.makedirs(DP_DIR, exist_ok=True)

data = json.load(open(OUT, encoding='utf-8'))
provs = data['proveedores']


def curl(url, dest):
    r = subprocess.run(['curl', '-sL', '--max-time', '120', '-A', 'Mozilla/5.0', '-o', dest, '-w', '%{http_code}', url],
                       capture_output=True, text=True)
    return r.stdout.strip()


# ---------- 1) integrantes de consorcios ----------
miembros = {}
for y in YEARS:
    f = os.path.join(CONS_DIR, f'CONSORCIO{y}_0.xlsx')
    if not os.path.exists(f):
        print('descargando', y, curl(CONS_URL.format(y=y), f))
    wb = openpyxl.load_workbook(f, read_only=True)
    it = wb[wb.sheetnames[0]].iter_rows(values_only=True); next(it)
    for r in it:
        if r[1] is None or r[3] is None:
            continue
        m = miembros.setdefault(str(r[1]).strip(), {})
        m[str(r[3]).strip()] = str(r[4] or '').strip()
    wb.close()


# ---------- 2) datosperu.org ----------
def clean(s):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', s))).strip()


def anio(desde):
    m = re.search(r'(\d{4})', desde or '')
    return m.group(1) if m else None


def datosperu(ruc):
    """-> (texto, url) o (None, None). El slug es indiferente: datosperu redirige por RUC."""
    f = os.path.join(DP_DIR, f'{ruc}.html'); u = os.path.join(DP_DIR, f'{ruc}.url')
    if not os.path.exists(f) or os.path.getsize(f) < 5000:
        r = subprocess.run(['curl', '-sL', '--max-time', '60', '-A', 'Mozilla/5.0', '-o', f, '-w', '%{http_code} %{url_effective}',
                            f'https://www.datosperu.org/empresa-x-{ruc}.php'], capture_output=True, text=True)
        code, _, eff = r.stdout.strip().partition(' ')
        open(u, 'w').write(eff if code == '200' else '')
        time.sleep(1.2)
    url = open(u).read().strip() if os.path.exists(u) else ''
    t = open(f, encoding='utf-8', errors='ignore').read()
    if not url or ruc not in t:
        return None, None
    ejec = []
    i = t.find('PRINCIPALES EJECUTIVOS')
    if i >= 0:
        tb = t[i:t.find('</tbody>', i)]
        for tr in re.findall(r'<tr class="hover[^"]*">(.*?)</tr>', tb, re.S):
            tds = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)
            if len(tds) >= 3:
                nombre = clean(re.sub(r'<span class="bg-teal[^"]*">.*?</span>', '', tds[0], flags=re.S))
                ejec.append((nombre, clean(tds[1]), clean(tds[2])))
    socios = []
    j = t.find('SOCIOS Y ADMINISTRACI')
    if j >= 0:
        tb = t[j:t.find('</tbody>', j)]
        for tr in re.findall(r'<tr[^>]*data-fila[^>]*>(.*?)</tr>', tb, re.S):
            tds = [clean(x) for x in re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)]
            if len(tds) >= 2 and 'socio' in tds[0].lower() and tds[1] not in socios:
                socios.append(tds[1])
    partes = []
    if ejec:
        pref = [e for e in ejec if 'GERENTE GENERAL' in e[1].upper()] or \
               [e for e in ejec if 'GERENTE' in e[1].upper() or 'TITULAR' in e[1].upper()] or ejec
        n, c, d = pref[0]
        a = anio(d)
        partes.append(f"{c.capitalize()} {n.title()}" + (f" (desde {a})" if a else ''))
    if socios:
        extra = f" y {len(socios) - 4} mas" if len(socios) > 4 else ''
        partes.append('socios/accionistas segun RNP-OECE: ' + ', '.join(s.title() for s in socios[:4]) + extra)
    if not partes:
        return None, url
    return '; '.join(partes), url


n_cons = n_dp = 0
for p in provs[:TOP_N]:
    # se recalcula siempre (todo 'dueno' de este archivo es automatico y trazable a su fuente)
    p['dueno'] = p['fuente_dueno'] = None
    if p['tipo_proveedor_seace'].lower().startswith('consorcio'):
        ms = miembros.get(p['ruc'])
        if not ms:
            continue
        txt = []
        for mruc, mnom in ms.items():
            s = mnom
            if mruc.startswith('20'):
                d, _ = datosperu(mruc)
                if d:
                    s += f" ({d.split(';')[0]})"
            txt.append(s)
        p['dueno'] = 'Consorcio formado por ' + '; '.join(txt)
        p['fuente_dueno'] = ('Integrantes segun CONOSCE/OECE - Reporte de Consorcios (ruc_consorcio): '
                             'https://conosce.osce.gob.pe/buscador/assets/67ae6c4a/reportes/consorcios/ ; '
                             'representantes de integrantes segun datosperu.org')
        n_cons += 1
    elif p['ruc'].startswith('20'):
        d, url = datosperu(p['ruc'])
        if d:
            p['dueno'], p['fuente_dueno'] = d, url
            n_dp += 1

con = [p for p in provs if p.get('dueno')]
monto_con = sum(p['monto'] for p in con)
tot = data['totales']['monto_total']
cob = (f" Duenos/representantes ('dueno'): solo cuando es posible, para {len(con)} de {len(provs)} proveedores "
       f"({monto_con / tot * 100:.0f}% del monto adjudicado), revisados entre los {TOP_N} de mayor monto; consorcios con "
       "integrantes segun el Reporte de Consorcios de CONOSCE/OECE; personas juridicas con gerente general/representante "
       "y socios segun datosperu.org (fuente secundaria que replica SUNAT y el RNP-OECE; consultada nov-2026, puede estar "
       "desactualizada). 'dueno' null = no investigado o sin fuente confiable; las personas naturales no se enriquecen.")
nota = data['_meta']['nota'].split(' Duenos/representantes')[0]
data['_meta']['nota'] = nota + cob
json.dump(data, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(f'consorcios enriquecidos: {n_cons}; juridicas datosperu: {n_dp}; con dueno total: {len(con)} '
      f'({monto_con / tot * 100:.1f}% del monto)')
for p in provs[:TOP_N]:
    print(f"{p['monto']:>14,.2f} {p['ruc']:12} {p['nombre'][:45]:45} | {p.get('dueno') or '-'}")
