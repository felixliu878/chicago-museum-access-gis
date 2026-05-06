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

## Layer: community_access

Community-area polygons with population-weighted summaries of tract access
and demographic variables.
