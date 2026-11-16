#!/usr/bin/env python3
"""TUPA de la Universidad Nacional Agraria La Molina (UNALM) -> data/tupa-unalm.json

Fuente: portal de transparencia UNALM, Oficina de Planeamiento, sección TUPA:
  https://www.lamolina.edu.pe/Rectorado/transparencia2/ofic_planificacion/
  Anexo vigente publicado: TUPA_2020/ANEXO_TUPA_2020_Modificacion.pdf (97 pág., formato SUT/PCM)
  Resolución: TUPA_2020/RR-2020-0234.pdf (R. N.° 0234-2020-R-UNALM, 23-jun-2020)

El PDF se descarga a un directorio temporal, se convierte con `pdftotext -layout`
(poppler) y se parsea cada ficha: nombre, código, derecho de tramitación, plazo,
calificación y unidad responsable. El PDF no se guarda en el repo.

Uso: python3 etl/build_tupa_unalm.py [--pdf ruta_local.pdf]
Si la descarga falla, se escribe el JSON con procedimientos=[] y la URL para descarga manual.
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, '..', 'data', 'tupa-unalm.json')
PORTAL = 'https://www.lamolina.edu.pe/Rectorado/transparencia2/ofic_planificacion/'
PDF_URL = PORTAL + 'TUPA_2020/ANEXO_TUPA_2020_Modificacion.pdf'
RES_URL = PORTAL + 'TUPA_2020/RR-2020-0234.pdf'
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'


def descargar(dst):
    # curl: el servidor de lamolina.edu.pe no envía la cadena intermedia y urllib falla la verificación SSL
    subprocess.run(['curl', '-sSL', '--fail', '-A', UA, '--max-time', '180', '-o', dst, PDF_URL], check=True)
    with open(dst, 'rb') as f:
        data = f.read(8)
    if not data.startswith(b'%PDF'):
        raise RuntimeError('la respuesta no es un PDF')


def calif_corta(t):
    t = t.lower()
    if 'automática' in t or 'automatica' in t:
        return 'Aprobación automática'
    if 'positivo' in t:
        return 'Evaluación previa - silencio administrativo positivo'
    if 'negativo' in t:
        return 'Evaluación previa - silencio administrativo negativo'
    return t.strip()[:80] or None


def parse(txt):
    # quitar cabeceras/pies de página que cortan las fichas
    lines = [l for l in txt.splitlines()
             if not re.match(r'^\s*(pág\. \d+|Texto Único de Procedimientos Administrativos|"UNIVERSIDAD NACIONAL AGRARIA LA MOLINA")\s*$', l)]
    # inicio de cada ficha: línea "NOMBRE" seguida (≤3 líneas) de "Código: PA..."
    starts = []
    for i, l in enumerate(lines):
        m = re.match(r'^"(.+)"\s*$', l.strip())
        if m and any(re.match(r'^\s*Código:\s*\S+', x) for x in lines[i + 1:i + 4]):
            starts.append((i, m.group(1).strip()))
    procs = []
    for k, (i, nombre) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(lines)
        blk = lines[i:end]
        b = '\n'.join(blk)
        codigo = re.search(r'Código:\s*(\S+)', b).group(1)
        # --- derecho de tramitación
        pago = re.search(r'Pago por derecho de tramitación(.*?)Plazo de atención', b, re.S)
        pago_txt = pago.group(1) if pago else ''
        izq = '\n'.join(l[:80] for l in pago_txt.splitlines())  # columna izquierda (la derecha es modalidad de pago)
        montos = [float(x) for x in re.findall(r'Monto\s*-\s*S/\s*([\d,]+\.?\d*)', izq.replace(',', ''))]
        conceptos = re.findall(r'^\s*\*\s*(.+?)\s*$', izq, re.M)
        gratuito = bool(re.search(r'^\s*Gratuito', izq, re.M))
        if 'Costo por Reproducción' in izq:
            derecho = 0.0  # el trámite es gratuito; solo se paga el costo de reproducción
        elif montos:
            derecho = montos[0]
        elif gratuito:
            derecho = 0.0
        else:
            derecho = None
        detalle = None
        if len(montos) > 1 or conceptos:
            detalle = [{'concepto': c.strip(), 'monto_soles': m} for c, m in zip(conceptos, montos)] or \
                      [{'concepto': None, 'monto_soles': m} for m in montos]
        # --- plazo y calificación
        pz = re.search(r'Plazo de atención\s+Calificación del procedimiento\s*\n\s*\n?(.*?)\n\s*\n', b, re.S)
        plazo, calif = None, None
        if pz:
            first = pz.group(1)
            mp = re.match(r'\s*(\d+)\s*días? hábiles', first)
            plazo = int(mp.group(1)) if mp else None
            calif = calif_corta(re.sub(r'^\s*\d+\s*días? hábiles', '', first).strip())
        # --- unidad responsable
        unidad = None
        mu = re.search(r'Unidad de organización responsable de aprobar la solicitud[^\n]*\n\s*\n((?:.+\n)+)', b)
        if mu:
            us = [re.split(r'\s{3,}', l.strip())[0].strip() for l in mu.group(1).splitlines() if l[:60].strip()]
            unidad = ' / '.join(u for u in us if u) or None
        procs.append({
            'codigo': codigo,
            'nombre': nombre,
            'derecho_tramitacion_soles': derecho,
            'gratuito': derecho == 0.0,
            'montos_detalle': detalle,
            'plazo_dias': plazo,
            'plazo_unidad': 'días hábiles' if plazo is not None else None,
            'calificacion': calif,
            'unidad': unidad,
        })
    return procs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pdf', help='PDF local (si ya se descargó a mano)')
    a = ap.parse_args()
    base = {
        'fuente': 'Portal de Transparencia UNALM - Oficina de Planeamiento, sección "Texto Único de Procedimientos Administrativos TUPA 2020" '
                  '(anexo en formato del Sistema Único de Trámites/PCM).',
        'fuente_url': PDF_URL,
        'portal_url': PORTAL,
        'norma_aprobatoria': 'Resolución N.° 0234-2020-R-UNALM (modifica el TUPA aprobado por R. N.° 0061-2015-R-UNALM, '
                             'modificado por R. N.° 0451-2018-R-UNALM, que deja sin efecto)',
        'norma_url': RES_URL,
        'normas_historial': [
            {'accion': 'Aprueba', 'norma': '0061-2015-R-UNALM', 'fecha': '2015-05-04'},
            {'accion': 'Modifica', 'norma': '0451-2018-R-UNALM', 'fecha': '2018-08-08'},
            {'accion': 'Modifica', 'norma': '0234-2020-R-UNALM', 'fecha': '2020-06-23'},
        ],
        'fecha': '2020-06-23',
        'normas_posteriores_relacionadas': [
            {'norma': '0678-2024-R-UNALM', 'fecha': '2024-09-05', 'objeto': 'Modifica los formularios FUT SG-002 a SG-005 (grados y títulos); no cambia derechos de tramitación según su texto.',
             'url': 'https://www.lamolina.edu.pe/rectorado/transparencia2/rectorales/2024/setiembre/RR_2024_0678.pdf'},
            {'norma': '0124-2025-R-UNALM', 'fecha': '2025-03-20', 'objeto': 'Plan de trabajo para actualizar el TUPA en el Sistema Único de Trámites (SUT).',
             'url': 'https://www.lamolina.edu.pe/rectorado/transparencia2/rectorales/2025/marzo/0124-2025-R-UNALM.pdf'},
            {'norma': '0183-2025-R-UNALM', 'fecha': '2025-04-14', 'objeto': 'Directiva de lineamientos para implementar el TUPA de la UNALM en el SUT.',
             'url': 'https://www.lamolina.edu.pe/rectorado/secgeneral/resoluciones_2025/Directiva_%20Lineamientos_TUPA_RR_2025_0183.pdf'},
            {'norma': '0382-2025-R-UNALM', 'fecha': '2025-06-12', 'objeto': 'Modifica de nuevo los FUT SG-002 a SG-005 de Secretaría General.',
             'url': 'https://www.lamolina.edu.pe/rectorado/transparencia2/rectorales/2025/junio/TR%200382-2025-R-UNALM.pdf'},
        ],
        'extraido': dt.date.today().isoformat(),
    }
    procs, err = [], None
    with tempfile.TemporaryDirectory() as td:
        pdf = a.pdf or os.path.join(td, 'tupa.pdf')
        try:
            if not a.pdf:
                descargar(pdf)
            txt = os.path.join(td, 'tupa.txt')
            subprocess.run(['pdftotext', '-layout', pdf, txt], check=True)
            procs = parse(open(txt, encoding='utf-8', errors='replace').read())
        except Exception as e:  # descarga bloqueada / sin poppler
            err = repr(e)[:200]
    n_g = sum(1 for p in procs if p['gratuito'])
    pagos = [p['derecho_tramitacion_soles'] for p in procs if p['derecho_tramitacion_soles']]
    out = dict(base, procedimientos=procs, resumen={
        'n_procedimientos': len(procs),
        'n_gratuitos': n_g,
        'n_con_pago': len(pagos),
        'derecho_max_soles': max(pagos) if pagos else None,
        'derecho_min_soles': min(pagos) if pagos else None,
        'n_aprobacion_automatica': sum(1 for p in procs if p['calificacion'] == 'Aprobación automática'),
        'n_evaluacion_previa': sum(1 for p in procs if (p['calificacion'] or '').startswith('Evaluación previa')),
    }, nota=(
        'TUPA vigente publicado en el portal de transparencia de la UNALM al ' + base['extraido'] + ': versión 2020 '
        '(R. 0234-2020-R-UNALM). En 2025 la UNALM inició la actualización del TUPA en el Sistema Único de Trámites '
        '(R. 0124-2025 y 0183-2025), pero no se encontró publicada una resolución que apruebe un TUPA nuevo; si se aprueba, '
        'este archivo queda desactualizado. Montos en soles tal como figuran en el anexo 2020 (calculados con la UIT 2020 de '
        'S/ 4,300; por D.S. 062-2009-PCM el cambio de UIT no modifica automáticamente el derecho de tramitación). '
        'derecho_tramitacion_soles = primer monto de la ficha (0 = gratuito; null = no se pudo leer); si hay varios conceptos '
        'ver montos_detalle. Extracción automática con pdftotext: verificar contra el PDF antes de citar un caso puntual. '
        'gob.pe/unalm no muestra el TUPA en HTML sin JavaScript; no se usó.'
        + (f' ERROR al obtener el PDF ({err}): descargar a mano desde fuente_url y correr con --pdf.' if err else '')))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f'OK {os.path.abspath(OUT)}: {len(procs)} procedimientos ({n_g} gratuitos)' + (f' ERROR {err}' if err else ''))


if __name__ == '__main__':
    sys.exit(main())
