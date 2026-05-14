#!/usr/bin/env python3
"""Acquire, clean, and analyze Chicago museum/cultural access data.

This script intentionally keeps acquisition and analysis in one reproducible
entry point so the course project can be rebuilt from an empty data directory.
Maps are exported separately with QGIS in scripts/02_qgis_maps.py.
"""

from __future__ import annotations

import io
import json
import math
import re
import textwrap
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
import statsmodels.api as sm
from matplotlib import pyplot as plt
from pyproj import CRS
from shapely.geometry import Point
from shapely.ops import unary_union


ROOT = Path(__file__).resolve().parents[1]
DATA_RAW = ROOT / "data" / "raw"
DATA_PROCESSED = ROOT / "data" / "processed"
DATA_METADATA = ROOT / "data" / "metadata"
OUTPUT_TABLES = ROOT / "outputs" / "tables"
OUTPUT_FIGURES = ROOT / "outputs" / "figures"

CRS_WGS84 = "EPSG:4326"
CRS_ANALYSIS = "EPSG:26916"  # NAD83 / UTM zone 16N, meters; appropriate for Chicago.
METERS_PER_MILE = 1609.344
WALKING_MPH = 3.0


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    note: str


SOURCES = [
    Source(
        "U.S. Census Bureau ACS 2024 5-year detailed tables",
        "https://api.census.gov/data/2024/acs/acs5",
        "Tract-level demographic and socioeconomic variables for Cook County, IL.",
    ),
    Source(
        "U.S. Census Bureau 2024 TIGER/Line census tracts",
        "https://www2.census.gov/geo/tiger/TIGER2024/TRACT/tl_2024_17_tract.zip",
        "Official Illinois census tract geometry joined to ACS by GEOID.",
    ),
    Source(
        "City of Chicago Boundaries - Community Areas",
        "https://data.cityofchicago.org/resource/igwz-8jzy.geojson",
        "Official community-area polygons used for communication and rollups.",
    ),
    Source(
        "City of Chicago CTA L stops",
        "https://data.cityofchicago.org/resource/8pix-ypme.geojson",
        "Station points used as transit context in the QGIS project.",
    ),
    Source(
        "City of Chicago CTA L rail lines",
        "https://data.cityofchicago.org/resource/xbyr-jnvx.geojson",
        "Rail line geometries used as transit context in the QGIS project.",
    ),
    Source(
        "OpenStreetMap via Overpass API",
        "https://overpass-api.de/api/interpreter",
        "Museum, gallery, and arts-centre points extracted on the run date.",
    ),
]


ACS_VARIABLES = {
    "B01003_001E": "population",
    "B19013_001E": "median_income",
    "B17001_001E": "poverty_universe",
    "B17001_002E": "poverty_count",
    "B02001_001E": "race_universe",
    "B02001_002E": "white_alone",
    "B02001_003E": "black_alone",
    "B03003_001E": "hisp_universe",
    "B03003_003E": "hispanic",
    "B08201_001E": "hh_vehicle_universe",
    "B08201_002E": "hh_no_vehicle",
    "B08301_001E": "commute_universe",
    "B08301_010E": "commute_public_transit",
}


def ensure_dirs() -> None:
    for path in [DATA_RAW, DATA_PROCESSED, DATA_METADATA, OUTPUT_TABLES, OUTPUT_FIGURES]:
        path.mkdir(parents=True, exist_ok=True)


def get(url: str, *, timeout: int = 60, stream: bool = False) -> requests.Response:
    headers = {
        "User-Agent": "UChicago GIS final project museum access analysis (educational use)"
    }
    response = requests.get(url, headers=headers, timeout=timeout, stream=stream)
    response.raise_for_status()
    return response


def post(url: str, data: dict[str, Any], *, timeout: int = 120) -> requests.Response:
    headers = {
        "User-Agent": "UChicago GIS final project museum access analysis (educational use)"
    }
    response = requests.post(url, data=data, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response


def download_text(url: str, path: Path, *, timeout: int = 60) -> None:
    if path.exists():
        return
    response = get(url, timeout=timeout)
    path.write_text(response.text, encoding="utf-8")


def download_binary(url: str, path: Path, *, timeout: int = 120) -> None:
    if path.exists():
        return
    response = get(url, timeout=timeout)
    path.write_bytes(response.content)


def load_acs() -> pd.DataFrame:
    fields = ["NAME", *ACS_VARIABLES.keys()]
    url = (
        "https://api.census.gov/data/2024/acs/acs5"
        f"?get={','.join(fields)}&for=tract:*&in=state:17%20county:031"
    )
    raw_path = DATA_RAW / "census_acs_2024_cook_tracts.json"
    download_text(url, raw_path, timeout=120)
    rows = json.loads(raw_path.read_text(encoding="utf-8"))
    acs = pd.DataFrame(rows[1:], columns=rows[0])
    acs["GEOID"] = acs["state"] + acs["county"] + acs["tract"]
    for original, clean in ACS_VARIABLES.items():
        acs[clean] = pd.to_numeric(acs[original], errors="coerce")
    for col in ["median_income"]:
        acs.loc[acs[col] <= 0, col] = np.nan
    keep = ["GEOID", "NAME", *ACS_VARIABLES.values()]
    return acs[keep]


def load_tracts() -> gpd.GeoDataFrame:
    zip_path = DATA_RAW / "tl_2024_17_tract.zip"
    download_binary(SOURCES[1].url, zip_path, timeout=180)
    tracts = gpd.read_file(f"zip://{zip_path}")
    tracts = tracts[tracts["COUNTYFP"] == "031"].copy()
    return tracts.to_crs(CRS_WGS84)


def load_community_areas() -> gpd.GeoDataFrame:
    path = DATA_RAW / "chicago_community_areas.geojson"
    download_text(SOURCES[2].url + "?$limit=100000", path, timeout=120)
    ca = gpd.read_file(path)
    ca["community"] = ca["community"].str.title()
    ca["community_num"] = pd.to_numeric(ca["area_num_1"], errors="coerce").astype("Int64")
    return ca[["community_num", "community", "geometry"]].to_crs(CRS_WGS84)


def load_cta_stations() -> gpd.GeoDataFrame:
    path = DATA_RAW / "cta_l_stops.geojson"
    download_text(SOURCES[3].url + "?$limit=100000", path, timeout=120)
    stations = gpd.read_file(path).to_crs(CRS_WGS84)
    station_name = stations["station_descriptive_name"].fillna(stations["station_name"])
    stations["station_label"] = station_name.astype(str)
    return stations[["stop_id", "station_label", "red", "blue", "g", "brn", "p", "pnk", "o", "y", "geometry"]]


def load_cta_lines() -> gpd.GeoDataFrame:
    path = DATA_RAW / "cta_l_lines.geojson"
    download_text(SOURCES[4].url + "?$limit=100000", path, timeout=120)
    lines = gpd.read_file(path).to_crs(CRS_WGS84)
    return lines[["description", "legend", "type", "lines", "geometry"]]


def load_osm_institutions(city_boundary: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    raw_path = DATA_RAW / "osm_chicago_cultural_institutions_overpass.json"
    if not raw_path.exists():
        minx, miny, maxx, maxy = city_boundary.total_bounds
        bbox = f"{miny:.6f},{minx:.6f},{maxy:.6f},{maxx:.6f}"
        query = textwrap.dedent(
            f"""
            [out:json][timeout:120];
            (
              nwr["tourism"="museum"]({bbox});
              nwr["tourism"="gallery"]({bbox});
              nwr["amenity"="arts_centre"]({bbox});
              nwr["amenity"="arts_center"]({bbox});
            );
            out center tags;
            """
        ).strip()
        response = post(SOURCES[5].url, data={"data": query}, timeout=180)
        raw_path.write_text(response.text, encoding="utf-8")

    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []
    for element in payload.get("elements", []):
        tags = element.get("tags", {})
        lon = element.get("lon", element.get("center", {}).get("lon"))
        lat = element.get("lat", element.get("center", {}).get("lat"))
        if lon is None or lat is None:
            continue
        name = (
            tags.get("name")
            or tags.get("official_name")
            or tags.get("short_name")
            or tags.get("operator")
        )
        if not name:
            continue
        if tags.get("tourism") == "museum":
            osm_type = "Museum"
            universe = "Narrow museum"
        elif tags.get("tourism") == "gallery":
            osm_type = "Gallery"
            universe = "Broader cultural institution"
        elif tags.get("amenity") in {"arts_centre", "arts_center"}:
            osm_type = "Arts center"
            universe = "Broader cultural institution"
        else:
            osm_type = "Other cultural"
            universe = "Broader cultural institution"
        records.append(
            {
                "osm_id": f"{element.get('type')}/{element.get('id')}",
                "name": clean_name(str(name)),
                "osm_type": osm_type,
                "universe": universe,
                "website": tags.get("website") or tags.get("contact:website") or "",
                "source_tags": json.dumps(tags, sort_keys=True),
                "geometry": Point(float(lon), float(lat)),
            }
        )
    gdf = gpd.GeoDataFrame(records, crs=CRS_WGS84)
    if gdf.empty:
        raise RuntimeError("No OSM cultural institutions were returned.")

    # Keep only points inside the city boundary, then remove duplicate OSM geometry
    # and duplicate names at nearly identical locations.
    city = city_boundary[["geometry"]].copy()
    gdf = gpd.sjoin(gdf, city, how="inner", predicate="within").drop(columns=["index_right"])
    gdf_m = gdf.to_crs(CRS_ANALYSIS)
    gdf_m["x_50m"] = (gdf_m.geometry.x / 50).round().astype(int)
    gdf_m["y_50m"] = (gdf_m.geometry.y / 50).round().astype(int)
    gdf_m["name_key"] = gdf_m["name"].map(slug)
    gdf_m["rank"] = np.where(gdf_m["osm_type"] == "Museum", 0, 1)
    gdf_m = (
        gdf_m.sort_values(["name_key", "x_50m", "y_50m", "rank"])
        .drop_duplicates(["name_key", "x_50m", "y_50m"], keep="first")
        .drop(columns=["x_50m", "y_50m", "name_key", "rank"])
    )
    return gdf_m.to_crs(CRS_WGS84).reset_index(drop=True)


def clean_name(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return value


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def rate(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return np.where(denominator > 0, numerator / denominator, np.nan)


def prepare_tracts(
    tracts: gpd.GeoDataFrame, acs: pd.DataFrame, community_areas: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    ca_wgs = community_areas.to_crs(CRS_WGS84)
    city_union = unary_union(ca_wgs.geometry)
    city = gpd.GeoDataFrame({"name": ["Chicago"]}, geometry=[city_union], crs=CRS_WGS84)

    tracts = tracts.merge(acs, on="GEOID", how="left")
    tracts["rep_point"] = tracts.geometry.representative_point()
    tracts_points = gpd.GeoDataFrame(tracts.drop(columns="geometry"), geometry="rep_point", crs=CRS_WGS84)
    tracts_points = tracts_points.rename_geometry("geometry")
    in_city = gpd.sjoin(tracts_points[["GEOID", "geometry"]], city, how="inner", predicate="within")
    tracts = tracts[tracts["GEOID"].isin(in_city["GEOID"])].copy()

    # Clip only for map display and area/density; ACS estimates remain whole-tract estimates.
    tracts_clip = gpd.overlay(tracts, city, how="intersection", keep_geom_type=True)
    tracts_clip = tracts_clip.to_crs(CRS_ANALYSIS)
    tracts_clip["area_sqmi"] = tracts_clip.geometry.area / (METERS_PER_MILE**2)
    tracts_clip["density_sqmi"] = tracts_clip["population"] / tracts_clip["area_sqmi"]
    tracts_clip = tracts_clip.to_crs(CRS_WGS84)

    tracts_clip["poverty_rate"] = rate(tracts_clip["poverty_count"], tracts_clip["poverty_universe"])
    tracts_clip["black_share"] = rate(tracts_clip["black_alone"], tracts_clip["race_universe"])
    tracts_clip["white_share"] = rate(tracts_clip["white_alone"], tracts_clip["race_universe"])
    tracts_clip["hispanic_share"] = rate(tracts_clip["hispanic"], tracts_clip["hisp_universe"])
    tracts_clip["no_vehicle_rate"] = rate(tracts_clip["hh_no_vehicle"], tracts_clip["hh_vehicle_universe"])
    tracts_clip["transit_commute_rate"] = rate(
        tracts_clip["commute_public_transit"], tracts_clip["commute_universe"]
    )
    tracts_clip["log_median_income"] = np.log(tracts_clip["median_income"].where(tracts_clip["median_income"] > 0))

    # Community-area assignment by tract representative point.
    tract_points = gpd.GeoDataFrame(
        tracts_clip[["GEOID"]].copy(),
        geometry=tracts_clip.geometry.representative_point(),
        crs=CRS_WGS84,
    )
    assigned = gpd.sjoin(
        tract_points,
        community_areas[["community_num", "community", "geometry"]],
        how="left",
        predicate="within",
    ).drop(columns=["index_right"])
    tracts_clip = tracts_clip.merge(assigned.drop(columns="geometry"), on="GEOID", how="left")

    # Centrality control: distance from tract representative point to City Hall / the Loop.
    loop = gpd.GeoSeries([Point(-87.6298, 41.8781)], crs=CRS_WGS84).to_crs(CRS_ANALYSIS).iloc[0]
    reps_m = tracts_clip.to_crs(CRS_ANALYSIS).geometry.representative_point()
    tracts_clip["dist_loop_mi"] = reps_m.distance(loop) / METERS_PER_MILE
    return tracts_clip


def add_access_metrics(
    tracts: gpd.GeoDataFrame, institutions: gpd.GeoDataFrame
) -> tuple[gpd.GeoDataFrame, dict[str, float]]:
    tracts_m = tracts.to_crs(CRS_ANALYSIS).copy()
    reps = tracts_m.geometry.representative_point()
    inst_m = institutions.to_crs(CRS_ANALYSIS).copy()
    narrow = inst_m[inst_m["osm_type"] == "Museum"].copy()
    broad = inst_m.copy()
    if narrow.empty:
        raise RuntimeError("No museum-only institutions remain after filtering.")

    def nearest_and_counts(points: gpd.GeoSeries, inst: gpd.GeoDataFrame, prefix: str) -> None:
        inst_geoms = list(inst.geometry)
        nearest_dist = []
        count_05 = []
        count_1 = []
        count_2 = []
        for point in points:
            distances = np.array([point.distance(geom) for geom in inst_geoms], dtype=float)
            nearest_dist.append(float(np.nanmin(distances)))
            count_05.append(int(np.sum(distances <= 0.5 * METERS_PER_MILE)))
            count_1.append(int(np.sum(distances <= 1.0 * METERS_PER_MILE)))
            count_2.append(int(np.sum(distances <= 2.0 * METERS_PER_MILE)))
        tracts_m[f"{prefix}_dist_m"] = nearest_dist
        tracts_m[f"{prefix}_dist_mi"] = tracts_m[f"{prefix}_dist_m"] / METERS_PER_MILE
        tracts_m[f"{prefix}_walk_min"] = tracts_m[f"{prefix}_dist_mi"] / WALKING_MPH * 60
        tracts_m[f"{prefix}_count_0p5mi"] = count_05
        tracts_m[f"{prefix}_count_1mi"] = count_1
        tracts_m[f"{prefix}_count_2mi"] = count_2

    nearest_and_counts(reps, narrow, "museum")
    nearest_and_counts(reps, broad, "broad")
    tracts_m["museum_within_1mi"] = (tracts_m["museum_count_1mi"] > 0).astype(int)
    tracts_m["broad_within_1mi"] = (tracts_m["broad_count_1mi"] > 0).astype(int)
    tracts_m["access_gap_20min"] = (tracts_m["broad_walk_min"] > 20).astype(int)

    # A bivariate class used for the equity map. High poverty + long access gets
    # the darkest class; low poverty + short access gets the lightest class.
    tracts_m["poverty_tercile"] = tercile_labels(tracts_m["poverty_rate"], ["Low poverty", "Mid poverty", "High poverty"])
    tracts_m["access_tercile"] = tercile_labels(tracts_m["broad_walk_min"], ["Short access", "Mid access", "Long access"])
    tracts_m["bivar_class"] = tracts_m["poverty_tercile"] + " / " + tracts_m["access_tercile"]

    nn_stats = nearest_neighbor_stats(broad)
    return tracts_m.to_crs(CRS_WGS84), nn_stats


def tercile_labels(series: pd.Series, labels: list[str]) -> pd.Series:
    ranked = series.rank(method="first")
    try:
        return pd.qcut(ranked, 3, labels=labels).astype(str)
    except ValueError:
        return pd.Series(np.repeat(labels[1], len(series)), index=series.index)


def nearest_neighbor_stats(points: gpd.GeoDataFrame) -> dict[str, float]:
    city_area_sqmi = np.nan
    if len(points) < 2:
        return {}
    pts = points.to_crs(CRS_ANALYSIS)
    geoms = list(pts.geometry)
    nn = []
    for i, geom in enumerate(geoms):
        distances = [geom.distance(other) for j, other in enumerate(geoms) if i != j]
        nn.append(min(distances))
    observed_m = float(np.mean(nn))
    # Area is not available here; use the convex hull area as a conservative
    # pattern descriptor rather than a formal citywide point-process test.
    hull_area_m2 = pts.geometry.union_all().convex_hull.area
    density = len(points) / hull_area_m2
    expected_m = 1 / (2 * math.sqrt(density))
    ratio = observed_m / expected_m if expected_m else np.nan
    se = 0.26136 / math.sqrt(len(points) ** 2 / hull_area_m2)
    z = (observed_m - expected_m) / se if se else np.nan
    return {
        "broad_institution_count": float(len(points)),
        "observed_mean_nn_m": observed_m,
        "expected_mean_nn_m_convex_hull": expected_m,
        "nearest_neighbor_ratio": ratio,
        "nearest_neighbor_z_approx": z,
        "convex_hull_area_sqmi": hull_area_m2 / (METERS_PER_MILE**2),
    }


def weighted_mean(values: pd.Series, weights: pd.Series) -> float:
    mask = values.notna() & weights.notna() & (weights > 0)
    if not mask.any():
        return np.nan
    return float(np.average(values[mask], weights=weights[mask]))


def weighted_share(mask_values: pd.Series, weights: pd.Series) -> float:
    return weighted_mean(mask_values.astype(float), weights)


def summarize_by_groups(tracts: gpd.GeoDataFrame) -> pd.DataFrame:
    rows = []
    specs = [
        ("All tracts", pd.Series(True, index=tracts.index)),
        ("High-poverty tracts (top quartile)", tracts["poverty_rate"] >= tracts["poverty_rate"].quantile(0.75)),
        ("Low-poverty tracts (bottom quartile)", tracts["poverty_rate"] <= tracts["poverty_rate"].quantile(0.25)),
        ("High no-vehicle tracts (top quartile)", tracts["no_vehicle_rate"] >= tracts["no_vehicle_rate"].quantile(0.75)),
        ("Low no-vehicle tracts (bottom quartile)", tracts["no_vehicle_rate"] <= tracts["no_vehicle_rate"].quantile(0.25)),
        ("Majority Black tracts", tracts["black_share"] >= 0.50),
        ("Majority Hispanic tracts", tracts["hispanic_share"] >= 0.50),
        ("Majority White tracts", tracts["white_share"] >= 0.50),
    ]
    for label, mask in specs:
        sub = tracts.loc[mask.fillna(False)].copy()
        if sub.empty:
            continue
        rows.append(
            {
                "group": label,
                "tracts": int(len(sub)),
                "population": int(sub["population"].sum()),
                "mean_broad_walk_min_pop_weighted": weighted_mean(sub["broad_walk_min"], sub["population"]),
                "mean_museum_walk_min_pop_weighted": weighted_mean(sub["museum_walk_min"], sub["population"]),
                "share_pop_broad_within_1mi": weighted_share(sub["broad_within_1mi"], sub["population"]),
                "share_pop_museum_within_1mi": weighted_share(sub["museum_within_1mi"], sub["population"]),
                "mean_broad_count_1mi_pop_weighted": weighted_mean(sub["broad_count_1mi"], sub["population"]),
                "median_income_pop_weighted": weighted_mean(sub["median_income"], sub["population"]),
                "poverty_rate_pop_weighted": weighted_mean(sub["poverty_rate"], sub["population"]),
                "no_vehicle_rate_pop_weighted": weighted_mean(sub["no_vehicle_rate"], sub["population"]),
            }
        )
    return pd.DataFrame(rows)


def community_rollup(tracts: gpd.GeoDataFrame, community_areas: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    rows = []
    for (num, community), sub in tracts.dropna(subset=["community"]).groupby(["community_num", "community"]):
        pop = sub["population"]
        rows.append(
            {
                "community_num": int(num),
                "community": community,
                "population": float(pop.sum()),
                "tracts": int(len(sub)),
                "broad_walk_min": weighted_mean(sub["broad_walk_min"], pop),
                "museum_walk_min": weighted_mean(sub["museum_walk_min"], pop),
                "broad_within_1mi": weighted_share(sub["broad_within_1mi"], pop),
                "museum_within_1mi": weighted_share(sub["museum_within_1mi"], pop),
                "poverty_rate": weighted_mean(sub["poverty_rate"], pop),
                "no_vehicle_rate": weighted_mean(sub["no_vehicle_rate"], pop),
                "black_share": weighted_mean(sub["black_share"], pop),
                "hispanic_share": weighted_mean(sub["hispanic_share"], pop),
                "median_income": weighted_mean(sub["median_income"], pop),
            }
        )
    summary = pd.DataFrame(rows)
    ca = community_areas.merge(summary, on=["community_num", "community"], how="left")
    return ca


def run_models(tracts: gpd.GeoDataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    model_cols = [
        "broad_walk_min",
        "poverty_rate",
        "no_vehicle_rate",
        "black_share",
        "hispanic_share",
        "log_median_income",
        "density_sqmi",
        "dist_loop_mi",
        "population",
    ]
    df = tracts[model_cols].replace([np.inf, -np.inf], np.nan).dropna().copy()
    df = df[df["population"] > 0].copy()
    df["density_10k"] = df["density_sqmi"] / 10000
    xcols = [
        "poverty_rate",
        "no_vehicle_rate",
        "black_share",
        "hispanic_share",
        "log_median_income",
        "density_10k",
        "dist_loop_mi",
    ]
    X = sm.add_constant(df[xcols])
    y = df["broad_walk_min"]
    weights = df["population"]
    model = sm.WLS(y, X, weights=weights).fit(cov_type="HC1")
    rows = []
    labels = {
        "const": "Constant",
        "poverty_rate": "Poverty rate",
        "no_vehicle_rate": "No-vehicle household rate",
        "black_share": "Black population share",
        "hispanic_share": "Hispanic population share",
        "log_median_income": "Log median household income",
        "density_10k": "Population density (10k/sq mi)",
        "dist_loop_mi": "Distance to Loop (miles)",
    }
    for key, label in labels.items():
        rows.append(
            {
                "term": key,
                "label": label,
                "estimate": model.params.get(key, np.nan),
                "std_error": model.bse.get(key, np.nan),
                "p_value": model.pvalues.get(key, np.nan),
                "ci_low": model.conf_int().loc[key, 0] if key in model.params.index else np.nan,
                "ci_high": model.conf_int().loc[key, 1] if key in model.params.index else np.nan,
            }
        )
    meta = {
        "n_model_tracts": int(model.nobs),
        "r_squared": float(model.rsquared),
        "adj_r_squared": float(model.rsquared_adj),
        "dependent_variable": "broad_walk_min",
        "weight": "ACS tract population",
        "covariance": "HC1 robust",
    }
    return pd.DataFrame(rows), meta


def make_figures(tracts: gpd.GeoDataFrame, coefficients: pd.DataFrame) -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "figure.dpi": 180,
        }
    )
    fig, ax = plt.subplots(figsize=(7, 4.2))
    df = tracts[["poverty_tercile", "broad_walk_min"]].dropna()
    order = ["Low poverty", "Mid poverty", "High poverty"]
    data = [df.loc[df["poverty_tercile"] == label, "broad_walk_min"] for label in order]
    ax.boxplot(data, tick_labels=["Low", "Middle", "High"], showfliers=False, patch_artist=True)
    for patch, color in zip(ax.artists, ["#d8f0e3", "#f2d38f", "#d98b72"]):
        patch.set_facecolor(color)
    ax.set_title("Walking time to nearest cultural institution by tract poverty tercile")
    ax.set_xlabel("Tract poverty tercile")
    ax.set_ylabel("Minutes to nearest museum/gallery/arts center")
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(OUTPUT_FIGURES / "fig_05_access_by_poverty_boxplot.png", bbox_inches="tight")
    plt.close(fig)

    coeff = coefficients[~coefficients["term"].eq("const")].copy()
    coeff = coeff.sort_values("estimate")
    fig, ax = plt.subplots(figsize=(7, 4.6))
    y = np.arange(len(coeff))
    ax.errorbar(
        coeff["estimate"],
        y,
        xerr=[coeff["estimate"] - coeff["ci_low"], coeff["ci_high"] - coeff["estimate"]],
        fmt="o",
        color="#1f5b70",
        ecolor="#8aa7b2",
        capsize=3,
    )
    ax.axvline(0, color="#3a3a3a", linewidth=0.8)
    ax.set_yticks(y)
    ax.set_yticklabels(coeff["label"])
    ax.set_xlabel("Additional minutes to nearest broad cultural institution")
    ax.set_title("Population-weighted tract model coefficients")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(OUTPUT_FIGURES / "fig_06_regression_coefficients.png", bbox_inches="tight")
    plt.close(fig)


def write_outputs(
    tracts: gpd.GeoDataFrame,
    community_areas: gpd.GeoDataFrame,
    institutions: gpd.GeoDataFrame,
    cta_stations: gpd.GeoDataFrame,
    cta_lines: gpd.GeoDataFrame,
    group_summary: pd.DataFrame,
    community_summary: gpd.GeoDataFrame,
    coefficients: pd.DataFrame,
    model_meta: dict[str, Any],
    nn_stats: dict[str, float],
) -> None:
    gpkg = DATA_PROCESSED / "chicago_museum_access.gpkg"
    if gpkg.exists():
        gpkg.unlink()

    city_boundary = gpd.GeoDataFrame(
        {"name": ["Chicago"]},
        geometry=[unary_union(community_areas.geometry)],
        crs=community_areas.crs,
    )
    layers = [
        (tracts, "tracts_access"),
        (community_summary, "community_access"),
        (institutions, "cultural_institutions"),
        (institutions[institutions["osm_type"] == "Museum"], "museums_only"),
        (community_areas, "community_areas"),
        (city_boundary, "city_boundary"),
        (cta_lines, "cta_l_lines"),
        (cta_stations, "cta_l_stops"),
    ]
    for layer, name in layers:
        clean_layer = single_geometry(layer)
        clean_layer.to_file(gpkg, layer=name, driver="GPKG")

    group_summary.to_csv(OUTPUT_TABLES / "tbl_01_group_access_summary.csv", index=False)
    community_summary.drop(columns="geometry").to_csv(
        OUTPUT_TABLES / "tbl_02_community_access_summary.csv", index=False
    )
    coefficients.to_csv(OUTPUT_TABLES / "tbl_03_wls_model_coefficients.csv", index=False)

    key_findings = build_key_findings(tracts, group_summary, coefficients, model_meta, nn_stats)
    metadata = {
        "run_timestamp": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "crs_analysis": CRS.from_user_input(CRS_ANALYSIS).to_string(),
        "walking_speed_mph": WALKING_MPH,
        "sources": [source.__dict__ for source in SOURCES],
        "institution_counts": institutions["osm_type"].value_counts().to_dict(),
        "tract_count": int(len(tracts)),
        "community_area_count": int(community_areas["community_num"].nunique()),
        "model": model_meta,
        "nearest_neighbor": nn_stats,
        "key_findings": key_findings,
    }
    (DATA_METADATA / "analysis_summary.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    (DATA_METADATA / "data_dictionary.md").write_text(data_dictionary(), encoding="utf-8")


def single_geometry(layer: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Drop inactive geometry columns before writing to GeoPackage."""
    clean = layer.copy()
    active = clean.geometry.name
    extra_geometry_cols = [
        col for col in clean.columns if col != active and str(clean[col].dtype) == "geometry"
    ]
    if extra_geometry_cols:
        clean = clean.drop(columns=extra_geometry_cols)
    return clean


def build_key_findings(
    tracts: gpd.GeoDataFrame,
    group_summary: pd.DataFrame,
    coefficients: pd.DataFrame,
    model_meta: dict[str, Any],
    nn_stats: dict[str, float],
) -> dict[str, Any]:
    def group_value(group: str, col: str) -> float:
        return float(group_summary.loc[group_summary["group"] == group, col].iloc[0])

    high_pov_walk = group_value("High-poverty tracts (top quartile)", "mean_broad_walk_min_pop_weighted")
    low_pov_walk = group_value("Low-poverty tracts (bottom quartile)", "mean_broad_walk_min_pop_weighted")
    high_no_car_walk = group_value("High no-vehicle tracts (top quartile)", "mean_broad_walk_min_pop_weighted")
    low_no_car_walk = group_value("Low no-vehicle tracts (bottom quartile)", "mean_broad_walk_min_pop_weighted")
    all_within = group_value("All tracts", "share_pop_broad_within_1mi")
    access_gap_share = weighted_share(tracts["access_gap_20min"], tracts["population"])
    poverty_coef = coefficients.loc[coefficients["term"] == "poverty_rate", "estimate"].iloc[0]
    no_vehicle_coef = coefficients.loc[coefficients["term"] == "no_vehicle_rate", "estimate"].iloc[0]
    return {
        "high_vs_low_poverty_walk_min_difference": high_pov_walk - low_pov_walk,
        "high_vs_low_no_vehicle_walk_min_difference": high_no_car_walk - low_no_car_walk,
        "population_share_with_broad_access_within_1mi": all_within,
        "population_share_over_20min_from_broad_institution": access_gap_share,
        "poverty_model_coefficient_minutes_per_full_share": float(poverty_coef),
        "no_vehicle_model_coefficient_minutes_per_full_share": float(no_vehicle_coef),
        "model_r_squared": model_meta["r_squared"],
        "nearest_neighbor_ratio_broad": nn_stats.get("nearest_neighbor_ratio"),
    }


def data_dictionary() -> str:
    return textwrap.dedent(
        """
        # Data Dictionary

        Main processed file: `data/processed/chicago_museum_access.gpkg`

        ## Layer: tracts_access

        Census tracts whose representative point falls within the City of Chicago
        community-area boundary. Geometries are clipped to the city boundary for
        cartographic display; ACS estimates remain whole-tract estimates.

        Key derived fields:

        - `poverty_rate`: ACS poverty count divided by ACS poverty universe.
        - `black_share`, `white_share`, `hispanic_share`: race/ethnicity shares.
        - `no_vehicle_rate`: households without an available vehicle.
        - `transit_commute_rate`: workers commuting by public transportation.
        - `density_sqmi`: total population divided by clipped tract square miles.
        - `museum_walk_min`: walking minutes from tract representative point to
          nearest OSM `tourism=museum` feature.
        - `broad_walk_min`: walking minutes from tract representative point to
          nearest museum, gallery, or arts center.
        - `museum_count_1mi`, `broad_count_1mi`: institution counts within one
          mile of the tract representative point.
        - `bivar_class`: 3-by-3 poverty/access class for the bivariate equity map.

        ## Layer: cultural_institutions

        OSM point features extracted with Overpass from `tourism=museum`,
        `tourism=gallery`, `amenity=arts_centre`, and `amenity=arts_center`.
        Ways and relations are represented by their OSM center coordinates.
        Duplicate names at nearly identical locations are collapsed.

        ## Layer: cta_l_lines

        City of Chicago CTA rail line geometries used only for cartographic
        transit context. The route lines are not used in the access-time
        calculations.

        ## Layer: cta_l_stops

        City of Chicago CTA L stop points used only for cartographic transit
        context. The stop points are not used in the access-time calculations.

        ## Layer: community_access

        Community-area polygons with population-weighted summaries of tract access
        and demographic variables.
        """
    ).strip() + "\n"


def main() -> None:
    ensure_dirs()
    acs = load_acs()
    tracts = load_tracts()
    community_areas = load_community_areas()
    cta_stations = load_cta_stations()
    cta_lines = load_cta_lines()
    city_boundary = gpd.GeoDataFrame(
        {"name": ["Chicago"]},
        geometry=[unary_union(community_areas.geometry)],
        crs=community_areas.crs,
    )
    institutions = load_osm_institutions(city_boundary)
    tracts_prepared = prepare_tracts(tracts, acs, community_areas)
    tracts_access, nn_stats = add_access_metrics(tracts_prepared, institutions)
    group_summary = summarize_by_groups(tracts_access)
    community_summary = community_rollup(tracts_access, community_areas)
    coefficients, model_meta = run_models(tracts_access)
    make_figures(tracts_access, coefficients)
    write_outputs(
        tracts_access,
        community_areas,
        institutions,
        cta_stations,
        cta_lines,
        group_summary,
        community_summary,
        coefficients,
        model_meta,
        nn_stats,
    )
    print(json.dumps(json.loads((DATA_METADATA / "analysis_summary.json").read_text()), indent=2))


if __name__ == "__main__":
    main()
