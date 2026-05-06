# Chicago Museum and Cultural Access Final Project

This folder contains a completed GIS final project on spatial equity of access to museums, galleries, and arts centers in Chicago.

## Final deliverables

- Final paper PDF: `outputs/paper/museum_access_chicago.pdf`
- LaTeX source: `outputs/paper/museum_access_chicago.tex`
- QGIS project: `outputs/qgis/museum_access_chicago.qgz`
- QGIS map figures: `outputs/figures/fig_01_*_qgis.png` through `fig_04_*_qgis.png`, plus a museum-only sensitivity map
- Analysis tables: `outputs/tables/*.csv`
- Processed GeoPackage: `data/processed/chicago_museum_access.gpkg`

## Rebuild steps

From the project root:

```bash
python3 scripts/01_acquire_analyze.py
/Applications/QGIS-final-4_0_0.app/Contents/MacOS/python scripts/02_qgis_maps.py
cd outputs/paper
/Users/felix/.codex/plugins/cache/openai-bundled/latex-tectonic/0.1.0/bin/tectonic --outdir build museum_access_chicago.tex
cp build/museum_access_chicago.pdf museum_access_chicago.pdf
```

## Data sources

- U.S. Census Bureau ACS 2024 5-year detailed tables
- U.S. Census Bureau 2024 TIGER/Line census tracts
- City of Chicago community-area boundaries
- City of Chicago CTA L stop points
- OpenStreetMap cultural institution features extracted through Overpass API on May 6, 2026

See `data/metadata/data_dictionary.md` and `data/metadata/analysis_summary.json` for variable definitions, source notes, counts, and key findings.
