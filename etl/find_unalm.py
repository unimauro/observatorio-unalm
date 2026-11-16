#!/usr/bin/env python3
"""Identifica a la UNALM en OECE/CONOSCE (RUC + codigo_entidad) para filtrar adjudicaciones.

Fuente: CSV.gz normalizados de CONOSCE Datos Abiertos (generados por
licitaperu-alertas/seace-scraper/conosce_sync.py): entidades.csv.gz + adjudicaciones_<anio>.csv.gz.
Resultado verificado 16-nov-2026: RUC 20147897406, codigo_entidad 001886 (cod_consucode 1886,
codigo SIAF 0096), "UNIVERSIDAD NACIONAL AGRARIA LA MOLINA", La Molina, Lima.
Uso: ~/.venvs/observatorio-unalm/bin/python etl/find_unalm.py
"""
import csv, gzip, os, unicodedata
from collections import Counter

D = os.environ.get('CONOSCE_OUT', '/Users/unimauro/Documents/Repos/licitaperu-alertas/seace-scraper/data/conosce/out')


def norm(s):
    s = ''.join(c for c in unicodedata.normalize('NFD', str(s or '')) if unicodedata.category(c) != 'Mn')
    return s.upper()


def match(nombre):
    n = norm(nombre)
    # La Molina: evitar falsos positivos (Municipalidad de La Molina, UNA de la Selva, INIA La Molina...)
    return 'AGRARIA LA MOLINA' in n or ('UNIVERSIDAD' in n and 'MOLINA' in n)


print('--- entidades.csv.gz ---')
with gzip.open(f'{D}/entidades.csv.gz', 'rt', encoding='utf-8') as f:
    for r in csv.DictReader(f):
        if match(r['nombre']):
            print(r)

for y in (2023, 2024, 2025, 2026):
    hits = Counter()
    with gzip.open(f'{D}/adjudicaciones_{y}.csv.gz', 'rt', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            if match(r['entidad']) or r['entidad_ruc'] == '20147897406':
                hits[(r['codigo_entidad'], r['entidad_ruc'], r['entidad'])] += 1
    print(f'--- adjudicaciones {y} ---')
    for k, v in hits.most_common():
        print(v, k)
