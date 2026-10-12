# Madrid dataset build — 2026-10-12T13:44:18

Output `data/metadata/estates_madrid.parquet`: 35693 rows (32043 units + 3650 building rows).
Source kinds over the 36 columns: {'derived': 9, 'raw': 8, 'constant': 1, 'concept': 7, 'geo': 9, 'empty': 2}

Steps: {'certificates': 34180, 'with_date': 34180, 'inside_zones': 33945, 'units': 32043, 'building_rows': 3650}
Coordinates: {'parcel': 33861, 'postcode': 311, 'None': 8}

| column | source | fill (units) |
|---|---|---|
| `id` | derived | 1.0 |
| `meta_immobile` | derived | 1.0 |
| `numero_immobili_per_catasto` | derived | 1.0 |
| `id_list` | derived | 0.0 |
| `lista_file_ape` | raw:edif_codreg | 1.0 |
| `list_file_ape_filtered` | raw:edif_codreg | 1.0 |
| `list_file_ape_filtered_parsed` | raw:edif_codreg | 1.0 |
| `codice_comune` | constant | 1.0 |
| `foglio` | raw:edif_refcat[7:14] | 1.0 |
| `particella` | raw:edif_refcat[0:7] | 0.988 |
| `subalterno` | raw:edif_refcat[14:18] | 0.817 |
| `superficie_di_riferimento_mq` | concept:floor_area | 1.0 |
| `indirizzo` | raw:edif_direc | 1.0 |
| `numero_civico` | raw:edif_direc | 0.966 |
| `latitudine` | geo:catastro parcel / postcode centroid | 1.0 |
| `longitudine` | geo:catastro parcel / postcode centroid | 1.0 |
| `zona_omi` | geo:barrio | 1.0 |
| `tipologia_bene_immobile` | concept:building_type (+crosswalk) | 1.0 |
| `epoca_costruzione` | concept:construction_year | 0.991 |
| `data_decorrenza` | concept:registration_date | 1.0 |
| `classe_energetica_ape` | concept:energy_class | 1.0 |
| `epglnren_ape` | concept:primary_energy_total (per m²) | 1.0 |
| `epglren_ape` | empty | 0.0 |
| `emissioni_co2` | concept:co2_emissions_total (per m²) | 1.0 |
| `classe_target_ape` | empty | 0.0 |
| `commerciale` | geo:OSM POIs (Turin formula, percentile) | 1.0 |
| `educazione` | geo:OSM POIs (Turin formula, percentile) | 1.0 |
| `mobilita` | geo:OSM POIs (Turin formula, percentile) | 1.0 |
| `sanita` | geo:OSM POIs (Turin formula, percentile) | 1.0 |
| `sport` | geo:OSM POIs (Turin formula, percentile) | 1.0 |
| `verde` | geo:OSM POIs (Turin formula, percentile) | 1.0 |
| `ape_score_classe` | derived:concept:energy_class | 1.0 |
| `ape_score_impianto` | derived:concept:heating_system_type+main_heating_fuel | 1.0 |
| `ape_score_involucro` | derived:raw:cal_calefdem | 1.0 |
| `ape_score_rinnovabili` | derived:concept:pv_electricity_production_total+renewable_energy_share, raw:termica_calef | 1.0 |
| `ape_score_total` | derived | 1.0 |
