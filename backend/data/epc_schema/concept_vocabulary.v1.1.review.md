# Draft EPC concept vocabulary (1.1)
_model: gpt-5.4 · 8 cities · 75 concepts · generated 2026-07-01T17:19:05_

**Summary:** The draft vocabulary captures the strongest recurring EPC concepts across the eight cities: dates, construction period/year, asset type, floor area, climate zone, major energy and emissions indicators, ratings, and core building systems. The main caveats are methodological: area definitions differ sharply, rating classes and numeric scores are country-specific, and energy/emissions metrics vary in scope between primary/final/delivered and total versus intensity forms.

## energy

### `primary_energy_intensity` — Primary energy intensity  (4/8 cities · cross_city, conf=high · unit=`kWh/m2.year` · risk=🔴high)
Primary energy use per unit floor area, as reported by the certificate methodology.
- _comparability_: Primary energy scope differs across countries: total primary, fossil primary, or non-renewable primary. These are related but not equivalent; source qualifier must be respected.
  - **Amsterdam**: `PrimaireFossieleEnergie` [partial] _(fossil primary energy intensity)_
  - **Amsterdam**: `PrimaireFossieleEnergieEMGForfaitair` [partial] _(fossil primary energy intensity, forfaitary variant)_
  - **Barcelona**: `energia_prim_ria_no_renovable` [partial] _(non-renewable primary energy intensity)_
  - **London**: `energy_consumption_current` [partial] _(UK EPC energy consumption indicator)_ — Commonly an intensity-like indicator, but exact scope differs.
  - **Paris**: `conso_5_usages_par_m2_ep` [direct] _(5 uses primary energy intensity)_

### `final_energy_intensity` — Final energy intensity  (3/8 cities · limited_cross_city, conf=medium · unit=`kWh/m2.year` · risk=🔴high)
Final energy use per unit floor area, as reported by the certificate methodology.
- _comparability_: End-use coverage differs; some sources cover 5 uses, others broader/narrower scopes. Still, final energy intensity is often more interpretable than totals.
  - **Amsterdam**: `BerekendeEnergieverbruik` [partial] _(calculated energy use intensity)_ — Magnitude suggests intensity-like metric.
  - **Barcelona**: `consum_d_energia_final` [direct] _(final energy consumption intensity)_
  - **Paris**: `conso_5_usages_par_m2_ef` [direct] _(5 uses final energy intensity)_

### `space_heating_demand_intensity` — Space heating demand intensity  (3/8 cities · limited_cross_city, conf=medium · unit=`kWh/m2.year` · risk=🔴high)
Heating demand per unit floor area.
- _comparability_: Demand definitions and climate normalization differ across methodologies.
  - **Amsterdam**: `Warmtebehoefte` [direct] _(heat demand intensity)_
  - **Barcelona**: `energia_calefacci_demanda` [direct] _(heating demand intensity)_
  - **Madrid**: `elec_demcalef` [direct] _(heating demand indicator)_

### `primary_energy_total` — Primary energy total  (2/8 cities · limited_cross_city, conf=medium · unit=`kWh/year` · risk=🔴high)
Total primary energy consumption of the certified asset over a year.
- _comparability_: Scope differs by country and by end-use coverage. Some fields are non-renewable primary only or specific subsets of end uses.
  - **Dublin**: `PrimaryEnergyLighting` [partial] _(lighting only)_
  - **Dublin**: `PrimaryEnergyPumpsFans` [partial] _(pumps and fans only)_
  - **Dublin**: `PrimaryEnergyMainWater` [partial] _(main water heating only)_
  - **Dublin**: `PrimaryEnergyMainSpace` [partial] _(main space heating only)_
  - **Dublin**: `PrimaryEnergySecondarySpace` [partial] _(secondary space heating only)_
  - **Dublin**: `PrimaryEnergySupplementaryWater` [partial] _(supplementary water heating only)_
  - **Paris**: `conso_5_usages_ep` [direct] _(5 uses total primary energy)_

### `final_energy_total` — Final energy total  (2/8 cities · limited_cross_city, conf=medium · unit=`kWh/year` · risk=🔴high)
Total final energy consumption of the certified asset over a year.
- _comparability_: Coverage varies by end uses and treatment of renewable/self-generation. Dublin fields are end-use totals; Paris provides 5-use total.
  - **Dublin**: `TotalDeliveredEnergy` [partial] _(delivered energy total rather than final energy total)_
  - **Paris**: `conso_5_usages_ef` [direct] _(5 uses final energy total)_

### `delivered_energy_total` — Delivered energy total  (2/8 cities · limited_cross_city, conf=low · unit=`kWh/year` · risk=🔴high)
Total delivered energy to the asset over a year.
- _comparability_: Delivered energy is distinct from final or primary energy and may exclude/export on-site generation depending on methodology.
  - **Dublin**: `TotalDeliveredEnergy` [direct]
  - **Paris**: `conso_5_usages_ef` [partial] _(final energy rather than delivered energy)_

### `cooling_demand_intensity` — Cooling demand intensity  (2/8 cities · limited_cross_city, conf=medium · unit=`kWh/m2.year` · risk=🔴high)
Cooling demand per unit floor area.
- _comparability_: Cooling demand definitions differ and may be zero for many dwellings or climates.
  - **Barcelona**: `energia_refrigeraci_demanda` [direct]
  - **Madrid**: `elec_demrefrig` [direct] _(cooling demand indicator)_

### `space_heating_final_energy_total` — Space heating final energy total  (2/8 cities · limited_cross_city, conf=medium · unit=`kWh/year` · risk=🔴high)
Annual final energy consumption for space heating.
- _comparability_: Coverage of main vs supplementary systems differs. Some sources use delivered energy rather than final energy; these are mapped only partially.
  - **Dublin**: `DeliveredEnergyMainSpace` [partial] _(main space heating delivered energy)_
  - **Dublin**: `DeliveredEnergySecondarySpace` [partial] _(secondary space heating delivered energy)_
  - **Paris**: `conso_chauffage_ef` [direct] _(space heating final energy)_
  - **Paris**: `conso_chauffage_ef_energie_n1` [partial] _(carrier n1 contribution only)_
  - **Paris**: `conso_chauffage_ef_energie_n2` [partial] _(carrier n2 contribution only)_

### `domestic_hot_water_final_energy_total` — Domestic hot water final energy total  (2/8 cities · limited_cross_city, conf=medium · unit=`kWh/year` · risk=🔴high)
Annual final energy consumption for domestic hot water.
- _comparability_: Coverage of main vs supplementary systems differs. Delivered energy fields are only partial matches.
  - **Dublin**: `DeliveredEnergyMainWater` [partial] _(main water heating delivered energy)_
  - **Dublin**: `DeliveredEnergySupplementaryWater` [partial] _(supplementary water heating delivered energy)_
  - **Paris**: `conso_ecs_ef` [direct] _(domestic hot water final energy)_
  - **Paris**: `conso_ecs_ef_energie_n1` [partial] _(carrier n1 contribution only)_
  - **Paris**: `conso_ecs_ef_energie_n2` [partial] _(carrier n2 contribution only)_

### `lighting_final_energy_total` — Lighting final energy total  (2/8 cities · limited_cross_city, conf=medium · unit=`kWh/year` · risk=🔴high)
Annual final energy consumption for lighting.
- _comparability_: Residential lighting treatment differs and may be zero or excluded in some dwelling schemes.
  - **Dublin**: `DeliveredLightingEnergy` [partial] _(delivered lighting energy)_
  - **Paris**: `conso_eclairage_ef` [direct] _(lighting final energy)_

### `auxiliary_final_energy_total` — Auxiliary final energy total  (2/8 cities · limited_cross_city, conf=medium · unit=`kWh/year` · risk=🔴high)
Annual final energy consumption for auxiliaries such as pumps and fans.
- _comparability_: Auxiliary end-use definitions vary; Dublin field is delivered pumps/fans only.
  - **Dublin**: `DeliveredEnergyPumpsFans` [partial] _(pumps and fans delivered energy)_
  - **Paris**: `conso_auxiliaires_ef` [direct] _(auxiliaries final energy)_

### `renewable_energy_share` — Renewable energy share  (2/8 cities · limited_cross_city, conf=medium · unit=`%` · risk=🟠medium)
Share of energy demand or supply covered by renewable energy.
- _comparability_: Countries define renewable contribution differently; some fields may be percentages, fractions, or capped indicators. Source cleaning is required.
  - **Amsterdam**: `AandeelHernieuwbareEnergie` [direct] _(renewable share)_
  - **Amsterdam**: `AandeelHernieuwbareEnergieEMGForfaitair` [partial] _(forfaitary variant)_
  - **Dublin**: `RER` [partial] _(renewable energy ratio as fraction)_ — Requires conversion from fraction to percent.
  - **Dublin**: `SolarHeatFraction` [partial] _(solar-only share)_ — Narrower than total renewable share.

### `pv_electricity_production_total` — PV electricity production total  (2/8 cities · limited_cross_city, conf=medium · unit=`kWh/year` · risk=🟠medium)
Annual on-site photovoltaic electricity production.
- _comparability_: Some systems may report generated, credited, or self-consumed electricity differently.
  - **Madrid**: `elec_energia` [partial] _(electric contribution energy, often PV-related)_
  - **Paris**: `production_electricite_pv_kwhep_par_an` [direct] _(PV electricity production)_

### `space_heating_demand_total` — Space heating demand total  (1/8 cities · single_city, conf=high · unit=`kWh/year` · risk=🔴high)
Annual heating demand of the certified asset.
- _comparability_: Demand scope and modeling conventions differ by country.
  - **Paris**: `besoin_chauffage` [direct]

### `cooling_demand_total` — Cooling demand total  (1/8 cities · single_city, conf=high · unit=`kWh/year` · risk=🔴high)
Annual cooling demand of the certified asset.
- _comparability_: Demand scope and modeling conventions differ by country.
  - **Paris**: `besoin_refroidissement` [direct]

### `domestic_hot_water_demand_total` — Domestic hot water demand total  (1/8 cities · single_city, conf=high · unit=`kWh/year` · risk=🔴high)
Annual domestic hot water demand of the certified asset.
- _comparability_: Demand scope and standard use assumptions differ by methodology.
  - **Paris**: `besoin_ecs` [direct] _(ECS = domestic hot water)_

### `cooling_final_energy_total` — Cooling final energy total  (1/8 cities · single_city, conf=high · unit=`kWh/year` · risk=🔴high)
Annual final energy consumption for cooling.
- _comparability_: Cooling coverage differs and is often absent for many dwellings.
  - **Paris**: `conso_refroidissement_ef` [direct]

## systems

### `main_heating_fuel` — Main heating fuel or energy carrier  (4/8 cities · cross_city, conf=high · unit=`category` · risk=🟠medium)
Primary fuel or energy carrier used for space heating.
- _comparability_: Some fields indicate fuel, others broader carrier or generator energy. Community/district systems and mixed systems complicate direct comparison.
  - **Dublin**: `MainSpaceHeatingFuel` [direct]
  - **London**: `main_fuel` [direct] _(main fuel including community supply distinctions)_
  - **Madrid**: `calefac_vector` [direct] _(heating energy vector)_
  - **Paris**: `type_energie_principale_chauffage` [direct] _(principal heating energy)_

### `heating_system_type` — Heating system or generator type  (4/8 cities · cross_city, conf=high · unit=`category` · risk=🟠medium)
Type of main space-heating system or generator.
- _comparability_: Fields may describe generator type, distribution-emitter package, or overall heating system. They should be harmonized into broad classes only.
  - **Liège**: `device` [direct] _(main heating device)_
  - **London**: `mainheat_description` [partial] _(system description combining generator and distribution)_
  - **Madrid**: `calefac_tipo` [direct] _(heating generator type)_
  - **Paris**: `type_generateur_chauffage_principal` [direct] _(principal heating generator)_

### `solar_hot_water_present` — Solar hot water present  (4/8 cities · cross_city, conf=high · unit=`boolean` · risk=🟢low)
Whether solar thermal contributes to domestic hot water or hot water production.
- _comparability_: Some sources indicate any solar thermal presence, others a specific ECS solar installation. Contribution magnitude is not captured here.
  - **Barcelona**: `solar_termica` [direct]
  - **Dublin**: `SolarHotWaterHeating` [direct]
  - **London**: `solar_water_heating_flag` [direct]
  - **Paris**: `type_installation_solaire_n1` [partial] _(categorical installation type including solar ECS)_

### `main_hot_water_fuel` — Main hot water fuel or energy carrier  (3/8 cities · limited_cross_city, conf=high · unit=`category` · risk=🟠medium)
Primary fuel or energy carrier used for domestic hot water.
- _comparability_: Some fields indicate fuel, others broader carrier or generator energy.
  - **Dublin**: `MainWaterHeatingFuel` [direct]
  - **Madrid**: `acs_vector` [direct] _(DHW energy vector)_
  - **Paris**: `type_energie_principale_ecs` [direct] _(principal DHW energy)_

### `hot_water_system_type` — Hot water system or generator type  (3/8 cities · limited_cross_city, conf=medium · unit=`category` · risk=🟠medium)
Type of domestic hot water system or generator.
- _comparability_: Fields may describe generator type or full installation configuration.
  - **London**: `hotwater_description` [partial] _(system description)_
  - **Madrid**: `acs_tipo` [direct] _(DHW generator type)_
  - **Paris**: `type_generateur_chauffage_principal_ecs` [direct] _(principal DHW generator)_

### `ventilation_type` — Ventilation type  (3/8 cities · limited_cross_city, conf=high · unit=`category` · risk=🟠medium)
Type of ventilation system or method.
- _comparability_: System taxonomies differ, but broad categories are comparable.
  - **Dublin**: `VentilationMethod` [direct]
  - **London**: `mechanical_ventilation` [direct] _(includes natural as category)_
  - **Paris**: `type_ventilation` [direct]

### `cooling_system_type` — Cooling system or generator type  (2/8 cities · limited_cross_city, conf=high · unit=`category` · risk=🟠medium)
Type of cooling system or generator.
- _comparability_: Cooling descriptions can mix generator type and system configuration.
  - **Madrid**: `refrig_tipo` [direct] _(cooling generator/system type)_
  - **Paris**: `type_generateur_froid` [direct] _(cooling generator type)_

### `heating_system_efficiency` — Heating system efficiency  (2/8 cities · limited_cross_city, conf=medium · unit=`%` · risk=🟠medium)
Reported efficiency of the main space-heating system or generator.
- _comparability_: Some sources report seasonal/system efficiency, others generator efficiency or COP-like values; placeholder and outlier values occur.
  - **Dublin**: `HSMainSystemEfficiency` [direct] _(main heating system efficiency)_
  - **Madrid**: `calefac_rendim` [partial] _(heating performance/efficiency)_ — Contains placeholder values.
  - ~~Paris: `cop_generateur_n1_ecs_n1`~~ [rejected] — Not mapped.

### `hot_water_system_efficiency` — Hot water system efficiency  (2/8 cities · limited_cross_city, conf=medium · unit=`%` · risk=🟠medium)
Reported efficiency of the domestic hot water system or generator.
- _comparability_: Some sources report seasonal/system efficiency, others generator efficiency or placeholder values.
  - **Dublin**: `WHMainSystemEff` [direct] _(main DHW system efficiency)_
  - **Madrid**: `acs_rendim` [partial] _(DHW performance/efficiency)_ — Contains placeholder values.

### `solar_pv_present` — Solar PV present  (2/8 cities · limited_cross_city, conf=high · unit=`boolean` · risk=🟢low)
Whether on-site photovoltaic electricity generation is present.
- _comparability_: Presence is broadly comparable even though production accounting differs.
  - **Barcelona**: `solar_fotovoltaica` [direct]
  - **Paris**: `presence_production_pv` [direct]

### `low_energy_lighting_share` — Low-energy lighting share  (2/8 cities · limited_cross_city, conf=high · unit=`%` · risk=🟠medium)
Share of fixed lighting that is low-energy/efficient.
- _comparability_: Comparable as a share, though what qualifies as low-energy lighting may vary somewhat by methodology period.
  - **Dublin**: `LowEnergyLightingPercent` [direct]
  - **London**: `low_energy_lighting` [direct] _(share of low energy lighting)_

### `heating_installation_scope` — Heating installation scope  (1/8 cities · single_city, conf=medium · unit=`category` · risk=🟠medium)
Whether the heating installation is individual, collective/community, or mixed.
- _comparability_: Community, collective, and mixed typologies differ by registry but broad grouping is usually possible.
  - **Paris**: `type_installation_chauffage` [direct]
  - **Paris**: `type_installation_chauffage_n1` [partial] _(installation n1 detailed field)_
  - ~~London: `main_fuel`~~ [rejected] — Not mapped as a source due to weak directness.

### `hot_water_installation_scope` — Hot water installation scope  (1/8 cities · single_city, conf=high · unit=`category` · risk=🟠medium)
Whether the domestic hot water installation is individual, collective/community, or mixed.
- _comparability_: Community, collective, and mixed typologies differ by registry but broad grouping is usually possible.
  - **Paris**: `type_installation_ecs` [direct]
  - **Paris**: `type_installation_ecs_n1` [partial] _(installation n1 detailed field)_

## envelope

### `wall_area` — Wall area  (2/8 cities · limited_cross_city, conf=high · unit=`m2` · risk=🟠medium)
Reported total area of walls in the envelope or geometry model.
- _comparability_: Envelope segmentation and whether areas are exposed, all walls, or modeled wall types differ by methodology.
  - **Dublin**: `WallArea` [direct] _(aggregate wall area)_
  - **Dublin**: `FirstWallArea` [partial] _(first wall type area)_
  - **Dublin**: `SecondWallArea` [partial] _(second wall type area)_
  - **Dublin**: `ThirdWallArea` [partial] _(third wall type area)_
  - **Lisbon**: `parede_area_total` [direct] _(total wall area)_

### `roof_area` — Roof area  (2/8 cities · limited_cross_city, conf=high · unit=`m2` · risk=🟠medium)
Reported total area of roof elements in the envelope or geometry model.
- _comparability_: May represent exposed roof only, predominant roof type, or aggregated modeled roof elements.
  - **Dublin**: `RoofArea` [direct] _(aggregate roof area)_
  - **Dublin**: `PredominantRoofTypeArea` [partial] _(predominant roof type area)_
  - **Lisbon**: `cobertura_area_total` [direct] _(total roof area)_

### `floor_element_area` — Floor element area  (2/8 cities · limited_cross_city, conf=medium · unit=`m2` · risk=🟠medium)
Reported area of floor elements in the building envelope or geometry model.
- _comparability_: Can refer to exposed floors, intermediate floors, or specific floor elements rather than total internal floor area.
  - **Dublin**: `FloorArea` [direct] _(envelope/model floor element area)_ — Distinct from total dwelling floor area in this schema.
  - **Lisbon**: `pavimento_area_total` [direct] _(total floor element area)_

### `window_area` — Window or glazed area  (2/8 cities · limited_cross_city, conf=high · unit=`m2` · risk=🟠medium)
Reported total glazed/window area in the envelope or geometry model.
- _comparability_: Sources may include windows only or all glazed openings; exposed-area conventions differ.
  - **Dublin**: `WindowArea` [direct] _(window area)_
  - **Lisbon**: `envidracado_area_total` [direct] _(total glazed area)_

### `door_area` — Door area  (1/8 cities · single_city, conf=high · unit=`m2` · risk=🟠medium)
Reported total door area in the envelope or geometry model.
- _comparability_: Door definitions and whether internal/external doors are included vary.
  - **Dublin**: `DoorArea` [direct]
  - ~~Paris: `deperditions_portes`~~ [rejected] — Not a genuine area match; retained? No.

### `wall_u_value` — Wall U-value  (1/8 cities · single_city, conf=medium · unit=`W/m2K` · risk=🟠medium)
Thermal transmittance of wall elements.
- _comparability_: Sources may report representative, modeled, or type-specific wall U-values. Aggregation conventions differ.
  - **Dublin**: `UValueWall` [direct] _(aggregate/representative wall U-value)_
  - **Dublin**: `FirstWallUValue` [partial] _(first wall type U-value)_
  - **Dublin**: `SecondWallUValue` [partial] _(second wall type U-value)_
  - **Dublin**: `ThirdWallUValue` [partial] _(third wall type U-value)_

### `roof_u_value` — Roof U-value  (1/8 cities · single_city, conf=high · unit=`W/m2K` · risk=🟠medium)
Thermal transmittance of roof elements.
- _comparability_: Representative or modeled values may differ by methodology.
  - **Dublin**: `UValueRoof` [direct]

### `floor_u_value` — Floor U-value  (1/8 cities · single_city, conf=high · unit=`W/m2K` · risk=🟠medium)
Thermal transmittance of floor elements.
- _comparability_: Representative or modeled values may differ by methodology.
  - **Dublin**: `UValueFloor` [direct]
  - **Dublin**: `GroundFloorUValue` [partial] _(ground floor-specific U-value)_

### `window_u_value` — Window U-value  (1/8 cities · single_city, conf=high · unit=`W/m2K` · risk=🟠medium)
Thermal transmittance of windows/glazing.
- _comparability_: May represent whole-window or glazing-system values; methodologies differ.
  - **Dublin**: `UValueWindow` [direct]

### `door_u_value` — Door U-value  (1/8 cities · single_city, conf=high · unit=`W/m2K` · risk=🟠medium)
Thermal transmittance of doors.
- _comparability_: Representative or modeled values may differ by methodology.
  - **Dublin**: `UvalueDoor` [direct]

## location

### `municipality` — Municipality or commune  (4/8 cities · cross_city, conf=medium · unit=`category` · risk=🟢low)
Municipality, commune, or city name/code associated with the property.
- _comparability_: Administrative levels differ, but all mapped fields identify the municipality/local authority area around the property.
  - **Barcelona**: `poblacio` [direct] _(municipality name)_
  - **Barcelona**: `codi_poblacio` [partial] _(municipality code)_
  - **London**: `local_authority_label` [partial] _(local authority rather than municipality)_
  - **London**: `local_authority` [partial] _(local authority code)_
  - **Madrid**: `edif_muni` [direct]
  - **Paris**: `nom_commune_ban` [direct] _(normalized commune)_
  - **Paris**: `nom_commune_brut` [partial] _(raw commune text)_

### `climate_zone` — Climate zone  (4/8 cities · cross_city, conf=high · unit=`category` · risk=🔴high)
Climate zone classification used in the EPC methodology.
- _comparability_: Climate zone taxonomies differ by country and season; values are not directly comparable without country-specific decoding.
  - **Barcelona**: `zona_climatica` [direct] _(single climate zone code)_
  - **Lisbon**: `Zona Climática de Inverno` [partial] _(winter climate zone)_
  - **Lisbon**: `Zona Climática de Verão` [partial] _(summer climate zone)_
  - **Madrid**: `edif_zonaclim` [direct] _(single climate zone code)_
  - **Paris**: `zone_climatique` [direct] _(French climate zone)_

### `postal_code` — Postal code  (2/8 cities · limited_cross_city, conf=high · unit=`postal_code` · risk=🟢low)
Postal code for the certified property address.
- _comparability_: Postal code formats differ by country but the concept is straightforward.
  - **Amsterdam**: `Postcode` [direct]
  - **Paris**: `code_postal_ban` [direct]
  - **Paris**: `code_postal_brut` [partial] _(raw postal code before geocoding)_

### `street_name` — Street name  (2/8 cities · limited_cross_city, conf=medium · unit=`text` · risk=🟢low)
Street name component of the property address.
- _comparability_: Address normalization varies but the concept is standard.
  - **Barcelona**: `adre_a` [direct]
  - **Paris**: `nom_rue_ban` [direct] _(BAN-normalized street name)_

### `house_number` — House number  (2/8 cities · limited_cross_city, conf=high · unit=`text` · risk=🟢low)
Primary house or street number of the property address.
- _comparability_: Some systems split house number and suffixes; only the main number is captured here.
  - **Amsterdam**: `Huisnummer` [direct]
  - **Paris**: `numero_voie_ban` [direct]

### `projected_x_coordinate` — Projected X coordinate  (2/8 cities · limited_cross_city, conf=medium · unit=`m` · risk=🟠medium)
Projected planar X/easting coordinate of the property location.
- _comparability_: Projected CRS differs across countries and sources; numeric values are not directly comparable without CRS metadata.
  - **Barcelona**: `utm_x` [direct] _(UTM/projection-specific)_
  - **Paris**: `coordonnee_cartographique_x_ban` [direct] _(BAN projected coordinate)_

### `projected_y_coordinate` — Projected Y coordinate  (2/8 cities · limited_cross_city, conf=medium · unit=`m` · risk=🟠medium)
Projected planar Y/northing coordinate of the property location.
- _comparability_: Projected CRS differs across countries and sources; numeric values are not directly comparable without CRS metadata.
  - **Barcelona**: `utm_y` [direct] _(UTM/projection-specific)_
  - **Paris**: `coordonnee_cartographique_y_ban` [direct] _(BAN projected coordinate)_

### `longitude` — Longitude  (1/8 cities · single_city, conf=high · unit=`degrees` · risk=🟢low)
Longitude coordinate of the property location in geographic CRS.
- _comparability_: Comparable when provided in geographic coordinates only.
  - **Barcelona**: `longitud` [direct]

### `latitude` — Latitude  (1/8 cities · single_city, conf=high · unit=`degrees` · risk=🟢low)
Latitude coordinate of the property location in geographic CRS.
- _comparability_: Comparable when provided in geographic coordinates only.
  - **Barcelona**: `latitud` [direct]

## emissions

### `co2_emissions_intensity` — CO2 emissions intensity  (5/8 cities · cross_city, conf=high · unit=`kgCO2e/m2.year` · risk=🔴high)
Annual CO2 or greenhouse-gas emissions per unit floor area.
- _comparability_: Emission factors, scope, and gas accounting differ by country and over time. Some sources may report CO2 only, others GES/CO2e.
  - **Amsterdam**: `BerekendeCO2Emissie` [partial] _(calculated CO2 emission intensity)_ — Magnitude suggests intensity-like field.
  - **Barcelona**: `emissions_de_co2` [direct] _(CO2 emissions intensity)_
  - **Dublin**: `CO2Rating` [direct] _(Irish CO2 rating intensity)_
  - **London**: `co2_emiss_curr_per_floor_area` [direct] _(current CO2 per floor area)_
  - **Paris**: `emission_ges_5_usages_par_m2` [direct] _(5 uses GHG intensity)_

### `co2_emissions_total` — CO2 emissions total  (2/8 cities · limited_cross_city, conf=medium · unit=`kgCO2e/year` · risk=🔴high)
Annual total CO2 or greenhouse-gas emissions of the certified asset.
- _comparability_: Scope and emission factors differ. Some fields are current/potential or subset-specific. Totals are less comparable than intensities unless floor-area basis and end-use coverage are harmonized.
  - **London**: `co2_emissions_current` [direct] _(current total emissions)_
  - **London**: `co2_emissions_potential` [partial] _(potential total emissions)_
  - **Paris**: `emission_ges_5_usages` [direct] _(5 uses total GHG emissions)_

### `space_heating_co2_total` — Space heating CO2 emissions total  (2/8 cities · limited_cross_city, conf=medium · unit=`kgCO2e/year` · risk=🔴high)
Annual CO2 or GHG emissions attributable to space heating.
- _comparability_: Heating-system scope and emission factors differ. Dublin reports main and secondary space separately.
  - **Dublin**: `CO2MainSpace` [partial] _(main space heating only)_
  - **Dublin**: `CO2SecondarySpace` [partial] _(secondary space heating only)_
  - **Paris**: `emission_ges_chauffage` [direct] _(space heating GHG emissions)_

### `domestic_hot_water_co2_total` — Domestic hot water CO2 emissions total  (2/8 cities · limited_cross_city, conf=medium · unit=`kgCO2e/year` · risk=🔴high)
Annual CO2 or GHG emissions attributable to domestic hot water.
- _comparability_: Water-heating scope and emission factors differ. Dublin reports main and supplementary components.
  - **Dublin**: `CO2MainWater` [partial] _(main water heating only)_
  - **Dublin**: `CO2SupplementaryWater` [partial] _(supplementary water heating only)_
  - **Paris**: `emission_ges_ecs` [direct] _(domestic hot water GHG emissions)_

### `lighting_co2_total` — Lighting CO2 emissions total  (2/8 cities · limited_cross_city, conf=medium · unit=`kgCO2e/year` · risk=🔴high)
Annual CO2 or GHG emissions attributable to lighting.
- _comparability_: Residential lighting treatment differs by methodology.
  - **Dublin**: `CO2Lighting` [partial] _(lighting CO2)_
  - **Paris**: `emission_ges_eclairage` [direct] _(lighting GHG emissions)_

### `auxiliary_co2_total` — Auxiliary CO2 emissions total  (2/8 cities · limited_cross_city, conf=medium · unit=`kgCO2e/year` · risk=🔴high)
Annual CO2 or GHG emissions attributable to auxiliaries such as pumps and fans.
- _comparability_: Auxiliary scope differs by methodology.
  - **Dublin**: `CO2PumpsFans` [partial] _(pumps and fans CO2)_
  - **Paris**: `emission_ges_auxiliaires` [direct] _(auxiliary GHG emissions)_

### `cooling_co2_total` — Cooling CO2 emissions total  (1/8 cities · single_city, conf=high · unit=`kgCO2e/year` · risk=🔴high)
Annual CO2 or GHG emissions attributable to cooling.
- _comparability_: Cooling is absent or negligible for many certificates; emission factors vary.
  - **Paris**: `emission_ges_refroidissement` [direct]

## other

### `certificate_context` — Certificate context or status  (4/8 cities · cross_city, conf=medium · unit=`category` · risk=🟠medium)
Administrative context of the certificate such as existing building, new building, final, provisional, or project/completed state.
- _comparability_: Administrative status labels vary strongly across national registries and should not be treated as a common rating outcome.
  - **Amsterdam**: `Status` [direct]
  - **Barcelona**: `tipus_tramit` [partial] _(processing/building completion context)_
  - **Dublin**: `TypeofRating` [direct]
  - **Madrid**: `edif_tiporeg` [direct]

### `certification_method` — Certification method or software  (4/8 cities · cross_city, conf=high · unit=`category` · risk=🔴high)
Methodology, calculation engine, or software used to produce the certificate.
- _comparability_: This concept is itself about methodology; it is useful for stratification and crosswalks, not for substantive comparison.
  - **Amsterdam**: `Berekeningstype` [direct] _(calculation method/version)_
  - **Barcelona**: `eina_de_certificacio` [direct] _(certification software)_
  - **Madrid**: `edif_proced` [direct] _(software/procedure)_
  - **Paris**: `modele_dpe` [partial] _(method family)_
  - **Paris**: `version_dpe` [partial] _(method version only)_

### `heating_cost_total` — Heating cost total  (2/8 cities · limited_cross_city, conf=high · unit=`currency/year` · risk=🟠medium)
Estimated annual energy cost for space heating.
- _comparability_: Cost estimates depend on national price assumptions, reference year, taxes, and tariffs. Cross-country comparison is weak without price normalization.
  - **London**: `heating_cost_current` [direct] _(current estimated annual cost)_
  - **Paris**: `cout_chauffage` [direct] _(annual heating cost)_

### `domestic_hot_water_cost_total` — Domestic hot water cost total  (2/8 cities · limited_cross_city, conf=high · unit=`currency/year` · risk=🟠medium)
Estimated annual energy cost for domestic hot water.
- _comparability_: Cost estimates depend on national price assumptions, reference year, taxes, and tariffs.
  - **London**: `hot_water_cost_current` [direct] _(current estimated annual cost)_
  - **Paris**: `cout_ecs` [direct] _(annual DHW cost)_

### `lighting_cost_total` — Lighting cost total  (2/8 cities · limited_cross_city, conf=high · unit=`currency/year` · risk=🟠medium)
Estimated annual energy cost for lighting.
- _comparability_: Cost estimates depend on national price assumptions, reference year, taxes, and tariffs.
  - **London**: `lighting_cost_current` [direct] _(current estimated annual cost)_
  - **Paris**: `cout_eclairage` [direct] _(annual lighting cost)_

### `total_energy_cost` — Total annual energy cost  (2/8 cities · limited_cross_city, conf=high · unit=`currency/year` · risk=🟠medium)
Estimated total annual energy cost for the certificate's covered end uses.
- _comparability_: Coverage and pricing assumptions differ substantially by country. This is useful within-country more than cross-country.
  - **Barcelona**: `cost_anual_aproximat_d_energia` [direct] _(approximate annual energy cost)_
  - **Paris**: `cout_total_5_usages` [direct] _(5 uses total cost)_

## temporal

### `assessment_date` — Assessment date  (6/8 cities · cross_city, conf=high · unit=`date` · risk=🟢low)
Date when the EPC/APE assessment or diagnostic visit was carried out.
- _comparability_: Administrative semantics vary slightly by country, but all mapped fields refer to the date of assessment/inspection/visit rather than validity end.
  - **Amsterdam**: `Opnamedatum` [direct] _(recorded as numeric yyyymmdd)_ — Inspection/recording date.
  - **Dublin**: `DateOfAssessment` [direct]
  - **Liège**: `certificate_date` [partial] _(certificate issue/assessment date merged in source)_ — Could reflect issuance rather than visit date.
  - **London**: `inspection_date` [direct]
  - **Madrid**: `edif_fecha` [direct]
  - **Paris**: `date_visite_diagnostiqueur` [direct] _(diagnostician visit date)_

### `construction_year` — Construction year  (6/8 cities · cross_city, conf=high · unit=`year` · risk=🟢low)
Reported year of original building construction.
- _comparability_: Some sources record exact year, others may contain placeholders, implausible values, or year of main construction only. Renovation is not consistently reflected.
  - **Amsterdam**: `Bouwjaar` [direct]
  - **Barcelona**: `any_construccio` [direct]
  - **Dublin**: `Year_of_Construction` [direct]
  - **Liège**: `build_year` [direct] — High missingness.
  - **Madrid**: `edif_año` [direct] — Contains some implausible values.
  - **Paris**: `annee_construction` [direct]

### `construction_period` — Construction period  (6/8 cities · cross_city, conf=high · unit=`category` · risk=🟠medium)
Reported construction age band or period category for the building.
- _comparability_: Period boundaries are country-specific and policy-specific; categories should not be compared as identical bins without recoding.
  - **Barcelona**: `normativa_construcci` [partial] _(regulatory era rather than pure construction period)_
  - **Lisbon**: `Período_Construcao_Geral` [direct]
  - **Liège**: `build_period` [direct] _(coarse 3-band period)_
  - **Liège**: `build_period_v2` [direct] _(finer multi-band period)_
  - **London**: `construction_age_band` [direct] _(England and Wales age band)_
  - **Madrid**: `edif_norma` [partial] _(regulatory era rather than pure construction period)_
  - **Paris**: `periode_construction` [direct]

### `registration_date` — Certificate registration date  (4/8 cities · cross_city, conf=medium · unit=`date` · risk=🟢low)
Date when the certificate was lodged, registered, received, or established in the administrative system.
- _comparability_: Different systems use establishment, reception, lodgement, or registration dates; these are administratively related and often close in time but not always identical.
  - **Amsterdam**: `Registratiedatum` [direct] _(numeric yyyymmdd)_
  - **Barcelona**: `data_entrada` [partial] _(entry date)_ — Likely intake/entry date.
  - **London**: `lodgement_date` [direct]
  - **London**: `lodgement_datetime` [direct] _(datetime version)_
  - **Paris**: `date_etablissement_dpe` [partial] _(certificate establishment date)_
  - **Paris**: `date_reception_dpe` [partial] _(certificate reception date)_

### `certificate_valid_until` — Certificate valid until  (2/8 cities · limited_cross_city, conf=high · unit=`date` · risk=🟢low)
Date until which the certificate remains valid.
- _comparability_: Validity periods are policy-defined and comparable as dates, though the underlying legal duration differs by country and period.
  - **Amsterdam**: `GeldigTot` [direct] _(numeric yyyymmdd)_
  - **Paris**: `date_fin_validite_dpe` [direct]

## geometry

### `floor_area` — Floor area  (7/8 cities · cross_city, conf=high · unit=`m2` · risk=🟠medium)
Reported area of the certified asset, usually dwelling/building usable, habitable, heated, thermal-zone, or cadastral area.
- _comparability_: Area definitions differ materially across registries: thermal-zone area, habitable area, ground floor area, total floor area, cadastral area, and heated floor area are not equivalent. They are grouped here only because they share unit and represent the main size measure of the certified asset.
  - **Amsterdam**: `GebruiksoppervlakteThermischeZone` [direct] _(thermal zone usable area)_
  - **Barcelona**: `metres_cadastre` [partial] _(cadastral area)_
  - **Dublin**: `GroundFloorArea(sq m)` [partial] _(ground floor area label used as main size field)_ — Likely principal area field though naming is ambiguous versus total dwelling area.
  - **Liège**: `total_heated_floor` [direct] _(total heated floor area)_
  - **London**: `total_floor_area` [direct] _(total floor area)_
  - **Madrid**: `edif_superf` [direct] _(building surface area)_
  - **Paris**: `surface_habitable_logement` [direct] _(dwelling habitable area)_
  - **Paris**: `surface_habitable_immeuble` [partial] _(building habitable area)_ — At building certificate level rather than dwelling.

### `number_of_storeys` — Number of storeys  (4/8 cities · cross_city, conf=medium · unit=`count` · risk=🟠medium)
Reported number of storeys or levels for the dwelling/building.
- _comparability_: Some sources refer to building storeys, others to flat storeys or dwelling levels; values are related but not identical.
  - **Dublin**: `NoStoreys` [direct] _(dwelling/building storeys)_
  - **Lisbon**: `Nr de pisos` [direct] _(building floors)_
  - **London**: `flat_storey_count` [partial] _(flat storey count)_
  - **Paris**: `nombre_niveau_immeuble` [partial] _(building levels)_
  - **Paris**: `nombre_niveau_logement` [partial] _(dwelling levels)_

### `floor_height` — Floor-to-ceiling or floor height  (3/8 cities · limited_cross_city, conf=medium · unit=`m` · risk=🟠medium)
Reported interior height or representative floor height for the certified asset.
- _comparability_: Paris reports ceiling height, while London and Dublin report floor heights that may represent different geometric constructs.
  - **Dublin**: `GroundFloorHeight` [partial] _(ground floor height)_
  - **Dublin**: `FirstFloorHeight` [partial] _(first floor height)_
  - **Dublin**: `SecondFloorHeight` [partial] _(second floor height)_
  - **Dublin**: `ThirdFloorHeight` [partial] _(third floor height)_
  - **London**: `floor_height` [partial] _(general floor height)_
  - **Paris**: `hauteur_sous_plafond` [direct] _(ceiling height)_

### `building_compactness` — Building compactness  (2/8 cities · limited_cross_city, conf=medium · unit=`ratio` · risk=🔴high)
Compactness indicator reported by the EPC model, typically reflecting form factor such as envelope-to-volume relation.
- _comparability_: Definition is methodology-specific and not clearly identical across countries, but both mapped fields appear to represent the same modeling construct.
  - **Amsterdam**: `Compactheid` [direct]
  - **Madrid**: `edif_compac` [direct] — Contains extreme outliers/placeholders.

## rating

### `energy_class` — Energy performance class  (7/8 cities · cross_city, conf=high · unit=`class` · risk=🔴high)
Ordinal energy label/class assigned by the national EPC methodology.
- _comparability_: Energy class scales and thresholds differ by country and over time. Some countries use A-G, others use sub-classes like A2/C1, and the underlying metric varies.
  - **Amsterdam**: `Energieklasse` [direct]
  - **Barcelona**: `qualificaci_de_consum_d` [direct] _(consumption-based energy class)_
  - **Dublin**: `EnergyRating` [direct] _(Irish BER class with subclasses)_
  - **Liège**: `e_spec_label` [direct] _(specific energy label)_
  - **London**: `current_energy_rating` [direct] _(current rating)_
  - **Madrid**: `cal_norenovglobal` [direct] _(non-renewable primary energy class)_
  - **Paris**: `etiquette_dpe` [direct] _(French DPE label)_

### `co2_class` — CO2 emissions class  (3/8 cities · limited_cross_city, conf=high · unit=`class` · risk=🔴high)
Ordinal class/label assigned to greenhouse gas emissions performance.
- _comparability_: CO2 class scales are country-specific and can reflect different scopes and thresholds.
  - **Barcelona**: `qualificacio_d_emissions` [direct]
  - **Barcelona**: `qualificaci_emissions` [direct] _(duplicate/variant emission class field)_
  - **Madrid**: `cal_co2global` [direct] _(global CO2 class)_
  - **Paris**: `etiquette_ges` [direct] _(GHG label)_

### `energy_efficiency_score` — Energy efficiency score or index  (3/8 cities · limited_cross_city, conf=medium · unit=`score` · risk=🔴high)
Numeric score/index used by the national EPC system to summarize energy efficiency or rating position.
- _comparability_: These numeric scores are highly country-specific: Dutch EnergieIndex, Irish BER kWh/m2-equivalent score, UK SAP-derived efficiency score, and similar constructs are not directly comparable.
  - **Amsterdam**: `EnergieIndex` [direct] _(Dutch energy index)_
  - **Amsterdam**: `EnergieIndexEMGForfaitair` [partial] _(forfaitary EMG variant)_
  - **Dublin**: `BerRating` [direct] _(Irish BER numeric score)_
  - **London**: `current_energy_efficiency` [direct] _(UK current efficiency score)_

## building_identity

### `building_type` — Building or dwelling type  (8/8 cities · cross_city, conf=high · unit=`category` · risk=🟠medium)
Reported typology of the certified residential asset, such as apartment, house, flat, fraction, or whole building.
- _comparability_: Cities mix dwelling-level and building-level typologies, and category systems differ substantially. Comparison requires harmonizing apartment/house/building distinctions.
  - **Amsterdam**: `Gebouwtype` [direct]
  - **Barcelona**: `us_edifici` [partial] _(use/type combined)_ — Mostly residential dwelling type in building context.
  - **Dublin**: `DwellingTypeDescr` [direct] _(dwelling type)_
  - **Lisbon**: `Tipo_Edificio` [direct] _(fraction vs building)_
  - **Liège**: `destination` [partial] _(destination/use class)_ — Contains apartment vs single-family house.
  - **London**: `property_type` [direct]
  - **Madrid**: `edif_tipo` [direct]
  - **Paris**: `type_batiment` [direct]

## ⚠️ Unmatched / needs human decision
- **potential or improved ratings/metrics** (London, Barcelona, Madrid): Several cities report potential/future ratings or parallel subsystem classes, but semantics vary and they are not consistently present enough to define a clean cross-city current-state concept pair.
- **subsystem-specific energy and emissions classes** (Barcelona, Madrid): Heating, cooling, ACS/DHW, and lighting class labels recur, but category meaning depends on local methodology and subsystem definitions; unification would produce many low-coverage concepts.
- **building envelope qualitative descriptors** (London, Paris): Both cities contain wall/roof/floor/window quality descriptions or efficiency grades, but one is free-text/ordinal component assessment and the other is modeled insulation quality; not confidently equivalent.
- **heating and cooling demand limit/reference thresholds** (Amsterdam, Madrid): Threshold/limit fields such as Eis* and cal_*limite are regulatory reference values rather than measured/certified property outcomes, and definitions differ too much.
- **renewable technology flags beyond solar** (Barcelona, Dublin, Paris): Biomass, geothermal, district network, CHP, wind turbine, and broader ENR categories recur, but each appears in different structure and specificity, preventing conservative unification.
- **air tightness and infiltration** (Dublin, Paris, London): Dublin has permeability test results and leakage proxies, Paris has air-renewal losses, and London has corridor/ventilation context; related theme but not the same measurable quantity.
- **address subcomponents like apartment unit, staircase, floor level** (Amsterdam, Barcelona, London, Paris): Fields such as escala, pis, porta, floor_level, numero_etage_appartement, huisletter, and house-number suffixes recur but represent heterogeneous addressing conventions and vertical position, not a single stable concept.
- **administrative assessor or provenance fields** (Amsterdam, Paris, London): Fields for certificate holder, geocoding provenance, UPRN source, or RNB provenance are administrative metadata with no robust common underlying quantity.
- **building identity codes** (Amsterdam, Dublin, Paris, London): BAG IDs, SA codes, commune codes, UPRN-related provenance, and similar identifiers recur, but they are jurisdiction-specific identifiers rather than a unified cross-city concept.
- **thermal comfort and summer overheating indicators** (Amsterdam, Paris): Temperatuuroverschrijding and indicateur_confort_ete are related to summer comfort/overheating but use different scales and constructs.
- **orientation-specific glazed area** (Lisbon, Paris): Lisbon provides façade glazing areas by orientation, while Paris has crossing-dwelling and solar protection indicators; not enough direct overlap for a unified concept.
- **detailed generator/installations hierarchies** (Madrid, Paris, Dublin): These datasets expose rich multi-generator, multi-installation hierarchies, but field structures are too source-specific to canonically align conservatively without inventing a complex nested schema.