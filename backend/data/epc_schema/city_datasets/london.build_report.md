# London dataset build — 2026-10-12T13:42:27

Output `data/metadata/estates_london.parquet`: 32778 rows (30474 units + 2304 building rows).
Source kinds over the 36 columns: {'derived': 10, 'raw': 8, 'geo': 10, 'constant': 1, 'concept': 6, 'empty': 1}

Steps: {'certificates': 31581, 'with_date': 31581, 'inside_zones': 31074, 'units': 30474, 'building_rows': 2304}
Coordinates: {'uprn': 29345, 'postcode': 1736, 'None': 500}

| column | source | fill (units) |
|---|---|---|
| `id` | derived | 1.0 |
| `meta_immobile` | derived | 1.0 |
| `numero_immobili_per_catasto` | derived | 1.0 |
| `id_list` | derived | 0.0 |
| `lista_file_ape` | raw:certificate_number | 1.0 |
| `list_file_ape_filtered` | raw:certificate_number | 1.0 |
| `list_file_ape_filtered_parsed` | raw:certificate_number | 1.0 |
| `codice_comune` | geo:ward's borough (LAD24CD) | 1.0 |
| `foglio` | constant | 0.0 |
| `particella` | derived:building group (postcode + UPRN point) | 0.944 |
| `subalterno` | raw:uprn | 0.944 |
| `superficie_di_riferimento_mq` | concept:floor_area | 1.0 |
| `indirizzo` | raw:address1-3 | 1.0 |
| `numero_civico` | raw:address1-3 | 0.803 |
| `latitudine` | geo:OS Open UPRN / postcode centroid | 1.0 |
| `longitudine` | geo:OS Open UPRN / postcode centroid | 1.0 |
| `zona_omi` | geo:ward | 1.0 |
| `tipologia_bene_immobile` | concept:building_type (+crosswalk) | 1.0 |
| `epoca_costruzione` | concept:construction_period (band -> year) | 1.0 |
| `data_decorrenza` | concept:registration_date | 1.0 |
| `classe_energetica_ape` | concept:energy_class | 1.0 |
| `epglnren_ape` | raw:energy_consumption_current | 1.0 |
| `epglren_ape` | empty | 0.0 |
| `emissioni_co2` | concept:co2_emissions_intensity | 1.0 |
| `classe_target_ape` | raw:potential_energy_rating | 1.0 |
| `commerciale` | geo:OSM POIs (Turin formula, percentile) | 1.0 |
| `educazione` | geo:OSM POIs (Turin formula, percentile) | 1.0 |
| `mobilita` | geo:OSM POIs (Turin formula, percentile) | 1.0 |
| `sanita` | geo:OSM POIs (Turin formula, percentile) | 1.0 |
| `sport` | geo:OSM POIs (Turin formula, percentile) | 1.0 |
| `verde` | geo:OSM POIs (Turin formula, percentile) | 1.0 |
| `ape_score_classe` | derived:concept:energy_class | 1.0 |
| `ape_score_impianto` | derived:concept:heating_system_type | 1.0 |
| `ape_score_involucro` | derived:raw:walls/roof/windows/floor_energy_eff | 1.0 |
| `ape_score_rinnovabili` | derived:concept:solar_pv_present+solar_hot_water_present, raw:wind_turbine_count | 1.0 |
| `ape_score_total` | derived | 1.0 |
