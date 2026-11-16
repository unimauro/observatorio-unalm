#!/usr/bin/env python3
"""Extrae de etl/work/ordenes-unalm.json (no publicado; contiene PII) solo lo del comedor -> data/comedor-ordenes-unalm.json (liviano para la web)."""
import json, os
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
W = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'work')
o = json.load(open(f'{W}/ordenes-unalm.json', encoding='utf-8'))
keep = ('fecha', 'orden', 'tipo', 'descripcion', 'monto', 'proveedor', 'ruc', 'comedor_cat', 'estado')
ords = sorted([{k: x.get(k) for k in keep} for x in o['ordenes'] if x['comedor'] and x['estado'] != 'Anulada'], key=lambda x: x['fecha'], reverse=True)
out = {'_meta': {k: o['_meta'][k] for k in o['_meta'] if k in ('fuente', 'fuente_url', 'periodo_solicitado', 'fecha_min', 'fecha_max', 'nota', 'caveats', 'generado')},
       'comedor': o['comedor'], 'totales': {k: o['totales'][k] for k in ('n_vigentes', 'monto_vigente_pen', 'n_menor_8uit', 'monto_menor_8uit_pen', 'por_anio')},
       'top_proveedores': o['top_proveedores'][:25], 'ordenes': ords}
# Cobertura real por mes (filas de la UNALM en cada archivo mensual): el gráfico pone null donde la fuente vino vacía
out['_meta']['archivos_mes'] = [{'mes': x['mes'], 'filas_unalm': x.get('filas_unalm')} for x in o['_meta'].get('archivos', [])]
utiles = [x['mes'] for x in out['_meta']['archivos_mes'] if (x['filas_unalm'] or 0) > 50]
out['_meta']['cobertura_util_desde'] = (utiles[0] + '-01') if utiles else None
out['_meta']['cobertura_util_nota'] = 'Primer mes con más de 50 órdenes de la UNALM en CONOSCE; antes hay meses vacíos o casi vacíos en la fuente.'
json.dump(out, open(f'{R}/comedor-ordenes-unalm.json', 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
print(len(ords), os.path.getsize(f'{R}/comedor-ordenes-unalm.json') // 1024, 'KB')
