#!/usr/bin/env python3
"""Construye data/proveedores-unalm.json desde los CSV.gz normalizados de CONOSCE
(generados por licitaperu-alertas/seace-scraper/conosce_sync.py), periodo 2023-2026.
Misma estructura que observatorio-unsaac/data/proveedores-unsaac.json.
Conserva 'dueno'/'fuente_dueno' ya investigados (por RUC) si el JSON ya existe.
Despues correr build_proveedores_unalm.py (integrantes de consorcios + duenos datosperu).

Uso: ~/.venvs/observatorio-unalm/bin/python etl/build_proveedores_csv.py [CORTE]
"""
import csv, gzip, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, '..', 'data', 'proveedores-unalm.json')
D = os.environ.get('CONOSCE_OUT', '/Users/unimauro/Documents/Repos/licitaperu-alertas/seace-scraper/data/conosce/out')
RUC_ENT = '20147897406'; CODIGO = '001886'   # ver find_unalm.py
YEARS = (2023, 2024, 2025, 2026)
CORTE = sys.argv[1] if len(sys.argv) > 1 else '2026-11'

duenos = {}
if os.path.exists(OUT):
    prev = json.load(open(OUT, encoding='utf-8'))
    duenos = {p['ruc']: (p.get('dueno'), p.get('fuente_dueno')) for p in prev['proveedores'] if p.get('dueno')}


def tp(ruc, tipo_seace):
    """Checklist adversarial: consorcios traen codigo SEACE de 7 digitos (no RUC 11) -> empresa."""
    ruc = str(ruc)
    if (tipo_seace or '').lower().startswith('consorcio'):
        return 'empresa'
    if len(ruc) != 11 or ruc[:2] not in ('10', '15', '17', '20'):
        return 'empresa'
    return 'natural' if ruc[:2] in ('10', '15', '17') else 'empresa'


rows = []
for y in YEARS:
    with gzip.open(f'{D}/adjudicaciones_{y}.csv.gz', 'rt', encoding='utf-8') as f:
        rows += [r for r in csv.DictReader(f) if r['entidad_ruc'] == RUC_ENT and r['codigo_entidad'] == CODIGO]
monedas = {r['moneda'] for r in rows}
assert monedas <= {'Soles'}, f'monedas no-PEN presentes: {monedas}'

agg = {}
for r in rows:
    ruc = r['ruc_proveedor']
    a = agg.setdefault(ruc, {'nombre': r['proveedor'], 'monto': 0.0, 'convs': set(), 'tipos': {}, 'objeto': {},
                             'anios': {}, 'tipo_seace': r['tipo_proveedor']})
    m = float(r['monto_adjudicado_item'] or 0)
    a['monto'] += m
    a['convs'].add(r['codigo_convocatoria'])
    a['tipos'][r['tipo_proceso']] = a['tipos'].get(r['tipo_proceso'], 0) + 1
    a['objeto'][r['objeto']] = a['objeto'].get(r['objeto'], 0) + 1
    a['anios'][r['anio']] = round(a['anios'].get(r['anio'], 0) + m, 2)

provs = []
for ruc, a in agg.items():
    d, f = duenos.get(ruc, (None, None))
    provs.append({'nombre': a['nombre'], 'ruc': ruc, 'monto': round(a['monto'], 2), 'n': len(a['convs']),
                  'tipos': a['tipos'], 'objeto': a['objeto'], 'por_anio': a['anios'],
                  'tipo_persona': tp(ruc, a['tipo_seace']), 'tipo_proveedor_seace': a['tipo_seace'],
                  'dueno': d, 'fuente_dueno': f})
provs.sort(key=lambda x: -x['monto'])
emp = [p for p in provs if p['tipo_persona'] == 'empresa']
nat = [p for p in provs if p['tipo_persona'] == 'natural']
por_anio = {}
for r in rows:
    por_anio[r['anio']] = round(por_anio.get(r['anio'], 0) + float(r['monto_adjudicado_item'] or 0), 2)

meta = {
 'fuente': 'OECE/OSCE - CONOSCE Datos Abiertos, reporte de Adjudicaciones (buena pro por item)',
 'fuente_url': 'https://conosce.osce.gob.pe/buscador/assets/67ae6c4a/reportes/adjudicaciones/',
 'entidad': 'Universidad Nacional Agraria La Molina (UNALM)',
 'ruc': RUC_ENT,
 'codigoentidad_conosce': int(CODIGO),
 'periodo': '2023-2026',
 'extraido': CORTE,
 'unidad_monto': 'Soles (PEN), monto adjudicado por item',
 'nota': ("Agregado desde reportes anuales CONOSCE de Adjudicaciones (nivel item de buena pro), archivos "
          "ADJUDICACIONES{2023,2024,2025,2026}_0.xlsx (2023: version descargada 27-oct-2026; 2024-2026: versiones "
          "regeneradas por OECE al 2/3-nov-2026; 2026 parcial: buenas pro otorgadas hasta esa fecha), filtrando "
          "codigoentidad 001886 / RUC 20147897406 (UNIVERSIDAD NACIONAL AGRARIA LA MOLINA - UNALM, entidad de Gobierno "
          "Nacional, La Molina, Lima; NO confundir con la Municipalidad de La Molina, el INIA, ni la Universidad Nacional "
          "Agraria de la Selva). 'monto' = suma de monto_adjudicado_item (todos los items de la entidad en el reporte, "
          "en soles). 'n' = numero de procesos (codigoconvocatoria) distintos en que ese proveedor obtuvo buena pro. "
          "'tipos' = items por tipo de proceso de seleccion. 'objeto' = items por objeto contractual (Bien/Servicio/Obra/"
          "Consultoria de Obra). tipo_persona por prefijo de RUC (20=empresa, 10/15/17=natural); consorcios (codigo SEACE "
          "de 7 digitos, no RUC) y no domiciliados clasificados como empresa. Monto adjudicado != monto contratado ni "
          "pagado. NO incluye ordenes de compra/servicio <=8 UIT (dataset aparte) ni compras por Catalogo Electronico.")
}
out = {'_meta': meta,
       'totales': {'monto_total': round(sum(p['monto'] for p in provs), 2), 'n_proveedores': len(provs),
                   'n_procesos': len({r['codigo_convocatoria'] for r in rows}), 'n_empresas': len(emp),
                   'n_personas_naturales': len(nat), 'monto_empresas': round(sum(p['monto'] for p in emp), 2),
                   'monto_personas_naturales': round(sum(p['monto'] for p in nat), 2), 'por_anio': por_anio},
       'top_personas': sorted([{'nombre': p['nombre'], 'ruc': p['ruc'], 'monto': p['monto'], 'n': p['n']} for p in nat], key=lambda x: -x['monto']),
       'proveedores': provs}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('item rows:', len(rows))
print(json.dumps(out['totales'], ensure_ascii=False))
print('--- top 30 (sin dueño marcado con *) ---')
for p in provs[:30]:
    print(f"{'*' if not p['dueno'] else ' '} {p['monto']:>14,.2f} {p['ruc']:12} {p['tipo_persona']:8} {p['tipo_proveedor_seace']:18} {p['por_anio']} {p['nombre']}")
