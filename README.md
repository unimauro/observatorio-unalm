# Observatorio UNALM

Portal ciudadano de transparencia de la **Universidad Nacional Agraria La Molina** (UNALM, pliego MEF 518, La Molina, Lima). Solo datos públicos. No es un portal oficial.

**Live:** https://unimauro.github.io/observatorio-unalm/

## Módulos y fuentes

| Módulo | Archivo | Fuente | Corte |
|---|---|---|---|
| Presupuesto 2012–2026 y en qué se gasta | `data/presupuesto-unalm.json` | MEF, Consulta Amigable SIAF (pliego 518) | 16-nov-2026 (2026 parcial) |
| Proveedores 2023–2026 y dueños (cuando es posible) | `data/proveedores-unalm.json` | OECE/CONOSCE adjudicaciones + Reporte de Consorcios + datosperu.org | nov-2026 |
| Comedor universitario | `data/comedor-*-unalm.json` | CONOSCE + OCDS (OECE) + Consulta Amigable MEF | 16-nov-2026 |
| Planilla nominal | `data/planilla-unalm.json` | Portal de Transparencia Estándar (id_entidad 10037) + AIRHSP | set-2026 (CAS ago-2026) |
| TUPA | `data/tupa-unalm.json` | Anexo TUPA 2020, R. N.° 0234-2020-R-UNALM | 16-nov-2026 |
| Investigación | `data/bibliometria.json` | OpenAlex (I79080234) | 16-nov-2026 |

Scripts de extracción en `etl/` (Python en un venv fuera del repo: `~/.venvs/observatorio-unalm`).

Lo que no está disponible se marca como tal en los JSON y en el sitio (p. ej. presupuesto por facultad, órdenes de compra desde jun-2025, citas por año de los pares). Nunca se publican nombres de estudiantes ni DNI.

Sitio estático (HTML + Chart.js), sin build. Correcciones: carlos@cardenas.pe
