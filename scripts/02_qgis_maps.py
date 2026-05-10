#!/usr/bin/env python3
"""Create QGIS project and export final map figures."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.PyQt.QtGui import QColor, QFont
from qgis.core import (
    QgsApplication,
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsFillSymbol,
    QgsGraduatedSymbolRenderer,
    QgsLayoutExporter,
    QgsLayoutItemLabel,
    QgsLayoutItemLegend,
    QgsLayoutItemMap,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsMarkerSymbol,
    QgsPrintLayout,
    QgsProject,
    QgsReferencedRectangle,
    QgsRendererCategory,
    QgsRendererRange,
    QgsSingleSymbolRenderer,
    QgsTextFormat,
    QgsUnitTypes,
    QgsVectorLayer,
)


ROOT = Path(__file__).resolve().parents[1]
GPKG = ROOT / "data" / "processed" / "chicago_museum_access.gpkg"
FIGURES = ROOT / "outputs" / "figures"
QGIS_OUT = ROOT / "outputs" / "qgis"
PROJECT_PATH = QGIS_OUT / "museum_access_chicago.qgz"
QGIS_PREFIX = "/Applications/QGIS-final-4_0_0.app/Contents/MacOS"
NORTH_ARROW_SVG = (
    "/Applications/QGIS-final-4_0_0.app/Contents/Resources/qgis/svg/arrows/NorthArrow_04.svg"
)


def init_qgis() -> QgsApplication:
    QgsApplication.setPrefixPath(QGIS_PREFIX, True)
    app = QgsApplication([], False)
    app.initQgis()
    return app


def load_layer(name: str, title: str) -> QgsVectorLayer:
    layer = QgsVectorLayer(f"{GPKG}|layername={name}", title, "ogr")
    if not layer.isValid():
        raise RuntimeError(f"Could not load layer {name} from {GPKG}")
    return layer


def fill_symbol(color: str, outline: str = "#ffffff", outline_width: float = 0.10) -> QgsFillSymbol:
    return QgsFillSymbol.createSimple(
        {
            "color": color,
            "outline_color": outline,
            "outline_width": str(outline_width),
            "outline_width_unit": "MM",
        }
    )


def marker_symbol(color: str, size: float, shape: str = "circle", outline: str = "#ffffff") -> QgsMarkerSymbol:
    return QgsMarkerSymbol.createSimple(
        {
            "name": shape,
            "color": color,
            "outline_color": outline,
            "outline_width": "0.25",
            "outline_width_unit": "MM",
            "size": str(size),
            "size_unit": "MM",
        }
    )


def style_boundaries(community: QgsVectorLayer, city: QgsVectorLayer) -> None:
    community.setRenderer(QgsSingleSymbolRenderer(fill_symbol("#f5f1e8", "#b9b2a6", 0.12)))
    city.setRenderer(QgsSingleSymbolRenderer(fill_symbol("#00000000", "#333333", 0.45)))


def style_institutions(layer: QgsVectorLayer) -> None:
    categories = [
        QgsRendererCategory("Museum", marker_symbol("#136f63", 2.4, "circle"), "Museum"),
        QgsRendererCategory("Gallery", marker_symbol("#c45a37", 2.0, "triangle"), "Gallery"),
        QgsRendererCategory("Arts center", marker_symbol("#4a63a8", 2.0, "square"), "Arts center"),
    ]
    layer.setRenderer(QgsCategorizedSymbolRenderer("osm_type", categories))


def graduated(layer: QgsVectorLayer, field: str, breaks: list[tuple[float, float, str, str]]) -> None:
    ranges = [
        QgsRendererRange(low, high, fill_symbol(color, "#ffffff", 0.04), label)
        for low, high, color, label in breaks
    ]
    renderer = QgsGraduatedSymbolRenderer(field, ranges)
    renderer.setMode(QgsGraduatedSymbolRenderer.Custom)
    layer.setRenderer(renderer)


def style_access_tracts(layer: QgsVectorLayer) -> None:
    graduated(
        layer,
        "broad_walk_min",
        [
            (0, 10, "#e8f6df", "0-10 min"),
            (10, 20, "#b9dfb3", "10-20 min"),
            (20, 30, "#f0d58a", "20-30 min"),
            (30, 45, "#df8f62", "30-45 min"),
            (45, 999, "#9f3b2f", "45+ min"),
        ],
    )


def style_museum_tracts(layer: QgsVectorLayer) -> None:
    graduated(
        layer,
        "museum_walk_min",
        [
            (0, 10, "#e6f0f5", "0-10 min"),
            (10, 20, "#b8d6e5", "10-20 min"),
            (20, 30, "#84afc8", "20-30 min"),
            (30, 45, "#4f7c9a", "30-45 min"),
            (45, 999, "#244660", "45+ min"),
        ],
    )


def style_community(layer: QgsVectorLayer) -> None:
    graduated(
        layer,
        "broad_walk_min",
        [
            (0, 10, "#edf8fb", "0-10 min"),
            (10, 20, "#b2e2e2", "10-20 min"),
            (20, 30, "#66c2a4", "20-30 min"),
            (30, 45, "#2ca25f", "30-45 min"),
            (45, 999, "#006d2c", "45+ min"),
        ],
    )


def style_bivariate(layer: QgsVectorLayer) -> None:
    colors = {
        "Low poverty / Short access": "#e8e8e8",
        "Low poverty / Mid access": "#ace4e4",
        "Low poverty / Long access": "#5ac8c8",
        "Mid poverty / Short access": "#dfb0d6",
        "Mid poverty / Mid access": "#a5add3",
        "Mid poverty / Long access": "#5698b9",
        "High poverty / Short access": "#be64ac",
        "High poverty / Mid access": "#8c62aa",
        "High poverty / Long access": "#3b4994",
    }
    categories = [
        QgsRendererCategory(value, fill_symbol(color, "#ffffff", 0.04), value)
        for value, color in colors.items()
    ]
    layer.setRenderer(QgsCategorizedSymbolRenderer("bivar_class", categories))


def clone_layer(layer_name: str, title: str, styler) -> QgsVectorLayer:
    layer = load_layer(layer_name, title)
    styler(layer)
    return layer


def add_label(layout: QgsPrintLayout, text: str, x: float, y: float, size: int, bold: bool = False, width: float = 180) -> QgsLayoutItemLabel:
    label = QgsLayoutItemLabel(layout)
    label.setText(text)
    font = QFont("Arial", size)
    font.setBold(bold)
    fmt = QgsTextFormat()
    fmt.setFont(font)
    fmt.setSize(size)
    label.setTextFormat(fmt)
    label.attemptMove(QgsLayoutPoint(x, y, QgsUnitTypes.LayoutMillimeters))
    label.attemptResize(QgsLayoutSize(width, 12, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(label)
    return label


def add_map_furniture(layout: QgsPrintLayout, map_item: QgsLayoutItemMap) -> None:
    """Add consistent north arrow and scale bar to each exported map layout."""
    scale_bar = QgsLayoutItemScaleBar(layout)
    scale_bar.setLinkedMap(map_item)
    scale_bar.setStyle("Line Ticks Up")
    scale_bar.applyDefaultSize()
    scale_bar.setUnits(QgsUnitTypes.DistanceMiles)
    scale_bar.setUnitLabel("mi")
    scale_bar.setNumberOfSegments(2)
    scale_bar.setNumberOfSegmentsLeft(0)
    scale_bar.setUnitsPerSegment(2)
    scale_bar.setHeight(2.0)
    scale_bar.setLineWidth(0.35)
    scale_bar.setBoxContentSpace(1.0)
    scale_bar.setLabelBarSpace(1.4)
    scale_bar.setFont(QFont("Arial", 7))
    scale_bar.setFontColor(QColor("#222222"))
    scale_bar.setLineColor(QColor("#222222"))
    scale_bar.setBackgroundEnabled(True)
    scale_bar.setBackgroundColor(QColor(255, 255, 255, 210))
    scale_bar.attemptMove(QgsLayoutPoint(12, 192, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(scale_bar)

    north_arrow = QgsLayoutItemPicture(layout)
    north_arrow.setPicturePath(NORTH_ARROW_SVG)
    north_arrow.setLinkedMap(map_item)
    north_arrow.setBackgroundEnabled(True)
    north_arrow.setBackgroundColor(QColor(255, 255, 255, 210))
    north_arrow.attemptMove(QgsLayoutPoint(194, 28, QgsUnitTypes.LayoutMillimeters))
    north_arrow.attemptResize(QgsLayoutSize(12, 14, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(north_arrow)


def export_map(
    project: QgsProject,
    name: str,
    title: str,
    subtitle: str,
    layers: list[QgsVectorLayer],
    legend_layers: list[QgsVectorLayer],
    legend_title: str,
) -> None:
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName(name)
    page = layout.pageCollection().pages()[0]
    page.setPageSize(QgsLayoutSize(279.4, 215.9, QgsUnitTypes.LayoutMillimeters))

    map_item = QgsLayoutItemMap(layout)
    map_item.setLayers(layers)
    map_item.attemptMove(QgsLayoutPoint(8, 24, QgsUnitTypes.LayoutMillimeters))
    map_item.attemptResize(QgsLayoutSize(202, 166, QgsUnitTypes.LayoutMillimeters))
    extent = project.mapLayersByName("Chicago boundary")[0].extent()
    extent.scale(1.05)
    map_item.setExtent(extent)
    map_item.setFrameEnabled(True)
    layout.addLayoutItem(map_item)

    add_label(layout, title, 8, 6, 17, True, 245)
    add_label(layout, subtitle, 8, 16, 9, False, 250)

    legend = QgsLayoutItemLegend(layout)
    legend.setTitle(legend_title)
    legend.setLinkedMap(map_item)
    legend.setAutoUpdateModel(False)
    root = legend.model().rootGroup()
    root.clear()
    for layer in legend_layers:
        root.addLayer(layer)
    legend.attemptMove(QgsLayoutPoint(214, 30, QgsUnitTypes.LayoutMillimeters))
    legend.attemptResize(QgsLayoutSize(58, 112, QgsUnitTypes.LayoutMillimeters))
    layout.addLayoutItem(legend)

    add_map_furniture(layout, map_item)

    source = (
        "Sources: ACS 2024 5-year, 2024 TIGER/Line, City of Chicago community areas, "
        "OpenStreetMap via Overpass. Distances are tract representative-point walking-time proxies."
    )
    add_label(layout, source, 8, 203, 7, False, 260)

    project.layoutManager().addLayout(layout)
    out = FIGURES / f"{name}.png"
    if out.exists():
        out.unlink()
    settings = QgsLayoutExporter.ImageExportSettings()
    settings.dpi = 300
    result = QgsLayoutExporter(layout).exportToImage(str(out), settings)
    if result != QgsLayoutExporter.Success:
        raise RuntimeError(f"Export failed for {name}: {result}")


def main() -> None:
    FIGURES.mkdir(parents=True, exist_ok=True)
    QGIS_OUT.mkdir(parents=True, exist_ok=True)
    app = init_qgis()
    project = QgsProject.instance()
    project.clear()
    project.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))

    community = clone_layer("community_areas", "Community areas", lambda layer: None)
    city = clone_layer("city_boundary", "Chicago boundary", lambda layer: None)
    style_boundaries(community, city)
    institutions = clone_layer("cultural_institutions", "Cultural institutions", style_institutions)
    cta = clone_layer("cta_l_stops", "CTA L stops", lambda layer: layer.setRenderer(
        QgsCategorizedSymbolRenderer(
            "",
            [QgsRendererCategory(None, marker_symbol("#111111", 1.1, "circle", "#f5f5f5"), "CTA L stop")],
        )
    ))

    tracts_access = clone_layer("tracts_access", "Broad access walking time", style_access_tracts)
    tracts_museum = clone_layer("tracts_access", "Museum-only walking time", style_museum_tracts)
    tracts_bivar = clone_layer("tracts_access", "Poverty and access", style_bivariate)
    community_access = clone_layer("community_access", "Community access", style_community)

    for layer in [
        community,
        city,
        institutions,
        cta,
        tracts_access,
        tracts_museum,
        tracts_bivar,
        community_access,
    ]:
        project.addMapLayer(layer)

    chicago_extent = city.extent()
    chicago_extent.scale(1.10)
    chicago_ref_extent = QgsReferencedRectangle(chicago_extent, QgsCoordinateReferenceSystem("EPSG:4326"))
    project.viewSettings().setDefaultViewExtent(chicago_ref_extent)
    project.viewSettings().setPresetFullExtent(chicago_ref_extent)
    project.viewSettings().setRestoreProjectExtentOnProjectLoad(True)

    export_map(
        project,
        "fig_01_institution_distribution_qgis",
        "Museum and Cultural Institution Distribution in Chicago",
        "OSM museums, galleries, and arts centers over official community areas",
        [institutions, cta, city, community],
        [institutions, cta],
        "Institution type",
    )
    export_map(
        project,
        "fig_02_broad_access_tracts_qgis",
        "Walking-Time Access to the Nearest Cultural Institution",
        "Minutes from each tract representative point to the nearest museum, gallery, or arts center",
        [institutions, city, tracts_access],
        [tracts_access, institutions],
        "Broad access",
    )
    export_map(
        project,
        "fig_03_poverty_access_bivariate_qgis",
        "Where High Poverty and Long Cultural Access Times Coincide",
        "Bivariate tract classes combine poverty-rate terciles with broad-access walking-time terciles",
        [institutions, city, tracts_bivar],
        [tracts_bivar, institutions],
        "Poverty / access class",
    )
    export_map(
        project,
        "fig_04_community_access_qgis",
        "Community-Area Summary of Cultural Access",
        "Population-weighted tract walking time to nearest broad cultural institution",
        [institutions, city, community_access],
        [community_access, institutions],
        "Mean broad access",
    )
    export_map(
        project,
        "fig_07_museum_only_access_qgis",
        "Museum-Only Walking-Time Access",
        "Sensitivity map using only OSM tourism=museum features",
        [institutions, city, tracts_museum],
        [tracts_museum, institutions],
        "Museum-only access",
    )

    project.write(str(PROJECT_PATH))
    app.exitQgis()
    print(f"Wrote QGIS project: {PROJECT_PATH}")
    print("Exported QGIS map figures to outputs/figures")


if __name__ == "__main__":
    main()
