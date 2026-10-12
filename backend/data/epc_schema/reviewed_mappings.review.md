# Reviewed mapping matrix (vocab 1.1) — 2026-07-02T18:08:57
_model gpt-5.4 · 75 concepts_
**Comparability:** {'conditional': 56, 'not_comparable': 18, 'comparable': 1}  **Cell verdicts:** {'downgrade': 44, 'keep': 193, 'flag': 17}


### `postal_code` — 🟢 comparable
_Postal code is a straightforward address concept across cities. Formats differ by country, but the values are directly comparable as postal-code identifiers when preserved as text._
  - Amsterdam: `Postcode` [keep→direct/high] → **preserve**
  - Barcelona: `codi_postal` [keep→direct/high] → **preserve**
  - Copenhagen: `PostalCode` [keep→direct/high] → **preserve**
  - London: `postcode` [keep→direct/high] → **preserve**
  - Madrid: `edif_codpost` [keep→direct/high] → **preserve**
  - Paris: `code_postal_ban` [keep→direct/high] → **preserve**

### `building_type` — 🟠 conditional
_All cities report some form of asset/building typology, but values are only conditionally comparable because scopes differ: some are dwelling/unit-level, some whole-building-level, some explicitly encode fraction vs whole building, and some include non-residential or broader property classes. Cross-city comparison requires a constrained harmonization to coarse categories and likely exclusion or flagging of mixed-scope/non-residential values._
  - Amsterdam: `Gebouwtype` [downgrade→partial/medium] → **preserve**
  - Barcelona: `us_edifici` [keep→direct/medium] → **preserve**
  - Dublin: `DwellingTypeDescr` [downgrade→partial/medium] → **preserve**
  - Liège: `destination` [keep→direct/medium] → **preserve**
  - Lisbon: `Tipo_Edificio` [downgrade→partial/medium] → **preserve**
  - London: `property_type` [keep→direct/medium] → **preserve**
  - Madrid: `edif_tipo` [downgrade→partial/medium] → **preserve**
  - Paris: `type_batiment` [keep→direct/high] → **preserve**
  - Turin: `tipologia_bene_immobile` [keep→partial/medium] → **preserve**

### `auxiliary_co2_total` — 🟠 conditional
_Both cities appear to report annual auxiliary-related emissions totals, but auxiliary scope is methodology-dependent and may include different end uses/devices (e.g. pumps only vs pumps and fans vs broader auxiliaries). Values are therefore only conditionally comparable._
  - Dublin: `CO2PumpsFans` [keep→direct/high] → **preserve**
  - Paris: `emission_ges_auxiliaires` [keep→direct/high] → **preserve**

### `co2_emissions_intensity` — 🟠 conditional
_All cities appear to report annual emissions per floor area, but underlying emission factors, gases included (CO2 vs GHG/CO2e), end-use scope, rating stage, and floor-area conventions differ by jurisdiction and period. Values are only conditionally comparable._
  - Amsterdam: `BerekendeCO2Emissie` [keep→uncertain/medium] → **preserve**
  - Barcelona: `emissions_de_co2` [keep→direct/high] → **preserve**
  - Dublin: `CO2Rating` [keep→direct/high] → **preserve**
  - London: `co2_emiss_curr_per_floor_area` [keep→direct/high] → **preserve**
  - Paris: `emission_ges_5_usages_par_m2` [downgrade→partial/high] → **preserve**
  - Turin: `emissioni_co2` [keep→direct/medium] → **preserve**

### `co2_emissions_total` — 🟠 conditional
_All mapped fields appear to represent annual total emissions, but scope differs materially: Paris is explicitly limited to 5 uses, London is current-rating stage, and national emission factors/gas accounting vary. Totals are therefore only conditionally comparable._
  - Copenhagen: `CalculatedEmission` [keep→direct/medium] → **preserve**
  - London: `co2_emissions_current` [downgrade→partial/medium] → **preserve**
  - Madrid: `norenov_co2global` [keep→direct/medium] → **preserve**
  - Paris: `emission_ges_5_usages` [downgrade→partial/high] → **preserve**

### `cooling_co2_total` — 🟠 conditional
_Cooling emissions are only sparsely reported and may be absent, negligible, or differently modeled across certificates. Barcelona is mapped from intensity rather than total, and emission factors vary. Values are only conditionally comparable._
  - Barcelona: `emissions_refrigeraci` [keep→partial/medium] → **derive** {'operation': 'multiply', 'operands': ['emissions_refrigeraci', 'concept:floor_area']}
  - Madrid: `norenov_co2refrig` [keep→direct/medium] → **preserve**
  - Paris: `emission_ges_refroidissement` [keep→direct/high] → **preserve**

### `domestic_hot_water_co2_total` — 🟠 conditional
_All fields relate to domestic hot water emissions, but Barcelona provides intensity rather than total, Dublin only covers the main component, and emission-factor/scope conventions differ. Values are only conditionally comparable._
  - Barcelona: `emissions_acs` [keep→partial/medium] → **derive** {'operation': 'multiply', 'operands': ['emissions_acs', 'concept:floor_area']}
  - Dublin: `CO2MainWater` [keep→partial/medium] → **preserve**
  - Madrid: `norenov_co2acs` [keep→direct/medium] → **preserve**
  - Paris: `emission_ges_ecs` [keep→direct/high] → **preserve**

### `lighting_co2_total` — 🟠 conditional
_Lighting emissions treatment differs substantially by methodology, especially across residential/non-residential contexts. Barcelona is mapped from intensity rather than total, and the remaining totals may still reflect different modeled scope. Values are only conditionally comparable._
  - Barcelona: `emissions_enllumenament` [keep→partial/medium] → **derive** {'operation': 'multiply', 'operands': ['emissions_enllumenament', 'concept:floor_area']}
  - Dublin: `CO2Lighting` [keep→direct/high] → **preserve**
  - Madrid: `norenov_co2ilu` [keep→direct/medium] → **preserve**
  - Paris: `emission_ges_eclairage` [keep→direct/high] → **preserve**

### `space_heating_co2_total` — 🟠 conditional
_All fields concern space-heating emissions, but Barcelona provides intensity, Dublin only the main space-heating component, and national emission-factor and system-scope conventions differ. Values are only conditionally comparable._
  - Barcelona: `emissions_calefacci` [keep→partial/medium] → **derive** {'operation': 'multiply', 'operands': ['emissions_calefacci', 'concept:floor_area']}
  - Dublin: `CO2MainSpace` [keep→partial/medium] → **preserve**
  - Madrid: `norenov_co2calef` [keep→direct/medium] → **preserve**
  - Paris: `emission_ges_chauffage` [keep→direct/high] → **preserve**

### `auxiliary_final_energy_total` — 🟠 conditional
_Both cities target auxiliary energy, but Dublin is delivered energy for pumps/fans only and explicitly used as a proxy for final energy, while Paris reports auxiliaries final energy total. End-use scope and energy basis differ, so values are only conditionally comparable._
  - Dublin: `DeliveredEnergyPumpsFans` [downgrade→partial/medium] → **preserve**
  - Paris: `conso_auxiliaires_ef` [keep→direct/high] → **preserve**

### `cooling_demand_intensity` — 🟠 conditional
_Both cities report cooling demand intensity, but cooling demand modeling conventions, climate assumptions, and treatment of low-cooling dwellings differ by methodology. Values can be compared only with caution._
  - Barcelona: `energia_refrigeraci_demanda` [keep→direct/high] → **preserve**
  - Madrid: `elec_demrefrig` [keep→direct/high] → **preserve**

### `cooling_final_energy_total` — 🟠 conditional
_Paris reports direct cooling final energy total, Barcelona appears to provide cooling final energy intensity, and Madrid derives total from intensity and floor area. Even after derivation, scope and cooling coverage differ, so values are only conditionally comparable._
  - Barcelona: `energia_refrigeraci` [keep→partial/medium] → **derive** {'operation': 'multiply', 'operands': ['energia_refrigeraci', 'concept:floor_area']}
  - Madrid: `final_refrig,edif_superf` [keep→partial/medium] → **derive** {'operation': 'multiply', 'operands': ['final_refrig', 'edif_superf']}
  - Paris: `conso_refroidissement_ef` [keep→direct/high] → **preserve**

### `domestic_hot_water_final_energy_total` — 🟠 conditional
_Paris reports direct DHW final energy total, Barcelona appears to provide intensity, Madrid derives total from intensity and area, and Dublin reports delivered main water-heating energy as a proxy. Energy basis and system coverage differ, so values are only conditionally comparable._
  - Barcelona: `energia_acs` [keep→partial/medium] → **derive** {'operation': 'multiply', 'operands': ['energia_acs', 'concept:floor_area']}
  - Dublin: `DeliveredEnergyMainWater` [downgrade→partial/medium] → **preserve**
  - Madrid: `final_acs,edif_superf` [keep→partial/medium] → **derive** {'operation': 'multiply', 'operands': ['final_acs', 'edif_superf']}
  - Paris: `conso_ecs_ef` [keep→direct/high] → **preserve**

### `final_energy_intensity` — 🟠 conditional
_All cities report some form of final energy intensity, but end-use coverage differs materially: Paris is explicitly 5 uses, Madrid is global as defined by its EPC methodology, Amsterdam is ambiguous between final and delivered, and other methodologies may include different boundaries. Values are interpretable but only conditionally comparable._
  - Amsterdam: `BerekendeEnergieverbruik` [flag→uncertain/low] → **preserve**
  - Barcelona: `consum_d_energia_final` [keep→direct/high] → **preserve**
  - Madrid: `final_global` [keep→direct/high] → **preserve**
  - Paris: `conso_5_usages_par_m2_ef` [keep→direct/high] → **preserve**

### `lighting_final_energy_total` — 🟠 conditional
_Paris reports direct lighting final energy total, Barcelona appears to provide intensity, Madrid derives total from intensity and area, and Dublin reports delivered lighting energy as a proxy. Residential lighting treatment and energy basis differ, so values are only conditionally comparable._
  - Barcelona: `energia_enllumenament` [keep→partial/medium] → **derive** {'operation': 'multiply', 'operands': ['energia_enllumenament', 'concept:floor_area']}
  - Dublin: `DeliveredLightingEnergy` [downgrade→partial/medium] → **preserve**
  - Madrid: `final_ilu,edif_superf` [keep→partial/medium] → **derive** {'operation': 'multiply', 'operands': ['final_ilu', 'edif_superf']}
  - Paris: `conso_eclairage_ef` [keep→direct/high] → **preserve**

### `pv_electricity_production_total` — 🟠 conditional
_Madrid appears to report direct annual PV electricity production, Dublin may report a first renewable production slot that is not certainly PV-only, and Paris labels PV production in primary-energy-equivalent units rather than direct electrical kWh. Values are only conditionally comparable._
  - Dublin: `FirstEnerProdDelivered` [flag→uncertain/low] → **preserve**
  - Madrid: `elec_energia` [keep→direct/high] → **preserve**
  - Paris: `production_electricite_pv_kwhep_par_an` [downgrade→partial/medium] → **preserve**

### `renewable_energy_share` — 🟠 conditional
_All cities report some renewable-related ratio, but definitions differ: whole-building share, renewable energy ratio, DHW-only thermal share, and an unclear Turin field. Percentage vs fraction scaling can be normalized where known, but scope differences make values only conditionally comparable._
  - Amsterdam: `AandeelHernieuwbareEnergie` [keep→direct/medium] → **preserve**
  - Dublin: `RER` [keep→direct/high] → **scale_fix** {'factor': 100}
  - Madrid: `termica_acscons` [downgrade→partial/medium] → **preserve**
  - Turin: `epglren_ape` [flag→uncertain/low] → **preserve**

### `space_heating_demand_intensity` — 🟠 conditional
_Both cities report heating demand intensity, but heating demand definitions, climate normalization, and modeling assumptions differ by methodology. Values are only conditionally comparable._
  - Barcelona: `energia_calefacci_demanda` [keep→direct/high] → **preserve**
  - Madrid: `elec_demcalef` [keep→direct/high] → **preserve**

### `space_heating_final_energy_total` — 🟠 conditional
_Paris reports direct space-heating final energy total, Barcelona appears to provide intensity, Madrid derives total from intensity and area, and Dublin reports delivered main space-heating energy as a proxy. System coverage and energy basis differ, so values are only conditionally comparable._
  - Barcelona: `energia_calefacci` [keep→partial/medium] → **derive** {'operation': 'multiply', 'operands': ['energia_calefacci', 'concept:floor_area']}
  - Dublin: `DeliveredEnergyMainSpace` [downgrade→partial/medium] → **preserve**
  - Madrid: `final_calef,edif_superf` [keep→partial/medium] → **derive** {'operation': 'multiply', 'operands': ['final_calef', 'edif_superf']}
  - Paris: `conso_chauffage_ef` [keep→direct/high] → **preserve**

### `door_area` — 🟠 conditional
_Only one city is present, so no demonstrated cross-city comparability. Even if additional cities are added, door-area values are conditionally comparable because inclusion of internal vs external doors and geometry/modeling conventions can differ._
  - Dublin: `DoorArea` [keep→direct/high] → **preserve**

### `door_u_value` — 🟠 conditional
_Only one city is present, so no demonstrated cross-city comparability. Door U-values are also methodology-sensitive: reported values may be representative/model-derived rather than directly measured, limiting strict comparability across cities._
  - Dublin: `UvalueDoor` [keep→direct/high] → **preserve**

### `floor_element_area` — 🟠 conditional
_Both cities appear to report envelope/element floor area rather than dwelling floor area, but floor-element scope can still differ across methodologies (exposed floors, intermediate floors, specific modeled floor elements). Values are comparable only if both sources use similar envelope-element inclusion rules._
  - Dublin: `FloorArea` [keep→direct/medium] → **preserve**
  - Lisbon: `pavimento_area_total` [keep→direct/high] → **preserve**

### `floor_u_value` — 🟠 conditional
_Only one city is present, so no demonstrated cross-city comparability. Floor U-values are methodology-dependent and may reflect representative or modeled values for different floor element types, so future cross-city comparisons should be conditional._
  - Dublin: `UValueFloor` [keep→direct/high] → **preserve**

### `roof_area` — 🟠 conditional
_Both cities report roof-element area, but roof-area values can differ depending on whether only exposed roof is counted, how multiple roof elements are aggregated, and geometry/model conventions. Comparable only under aligned roof-area scope._
  - Dublin: `RoofArea` [keep→direct/high] → **preserve**
  - Lisbon: `cobertura_area_total` [keep→direct/high] → **preserve**

### `roof_u_value` — 🟠 conditional
_Only one city is present, so no demonstrated cross-city comparability. Roof U-values may reflect representative or modeled values and may aggregate different roof constructions, so future comparability is conditional._
  - Dublin: `UValueRoof` [keep→direct/high] → **preserve**

### `wall_area` — 🟠 conditional
_Both cities report wall area, but wall-area values are only conditionally comparable because envelope segmentation and inclusion rules may differ (exposed walls only vs all walls; modeled wall types; treatment of party walls and openings)._
  - Dublin: `WallArea` [keep→direct/high] → **preserve**
  - Lisbon: `parede_area_total` [keep→direct/high] → **preserve**

### `wall_u_value` — 🟠 conditional
_Only one city is present, so no demonstrated cross-city comparability. Wall U-values are often representative or aggregate across wall types, and aggregation conventions differ, so strict comparability across cities is conditional._
  - Dublin: `UValueWall` [keep→direct/high] → **preserve**

### `window_area` — 🟠 conditional
_Both cities report glazed/window area, but values are only conditionally comparable because some methodologies may include windows only while others include all glazed openings, and exposed-area conventions may differ._
  - Dublin: `WindowArea` [keep→direct/high] → **preserve**
  - Lisbon: `envidracado_area_total` [keep→direct/high] → **preserve**

### `window_u_value` — 🟠 conditional
_Only one city is present, so no demonstrated cross-city comparability. Window U-values are methodology-sensitive and may represent whole-window values or glazing-system values, so future cross-city comparison is conditional._
  - Dublin: `UValueWindow` [keep→direct/high] → **preserve**

### `floor_area` — 🟠 conditional
_All cities report an area measure in m² for the certified asset, but the underlying area basis differs materially: thermal-zone usable, cadastral, heated, gross/ground floor, total floor, habitable, and reference area are not equivalent. Values can be compared only conditionally, with strong scope caveats and preferably within harmonized subsets._
  - Amsterdam: `GebruiksoppervlakteThermischeZone` [downgrade→partial/high] → **preserve**
  - Barcelona: `metres_cadastre` [downgrade→partial/medium] → **preserve**
  - Copenhagen: `TotalHeatedFloorArea` [downgrade→partial/high] → **preserve**
  - Dublin: `GroundFloorArea(sq m)` [downgrade→partial/medium] → **preserve**
  - Liège: `total_heated_floor` [downgrade→partial/high] → **preserve**
  - London: `total_floor_area` [keep→direct/high] → **preserve**
  - Madrid: `edif_superf` [downgrade→partial/medium] → **preserve**
  - Paris: `surface_habitable_logement` [downgrade→partial/high] → **preserve**
  - Turin: `superficie_di_riferimento_mq` [downgrade→partial/high] → **preserve**

### `floor_height` — 🟠 conditional
_All mapped fields relate to interior vertical dimension in meters, but Dublin reports ground-floor height only, London reports a representative floor height, and Paris reports ceiling/clear height. These are related but not guaranteed identical constructs._
  - Dublin: `GroundFloorHeight` [keep→partial/medium] → **preserve**
  - London: `floor_height` [keep→direct/high] → **preserve**
  - Paris: `hauteur_sous_plafond` [downgrade→partial/high] → **preserve**

### `number_of_storeys` — 🟠 conditional
_Storey/level counts are only conditionally comparable because some cities report building-level storeys while Paris reports dwelling-level levels. These measures are related but can differ materially, especially for flats within multi-storey buildings._
  - Dublin: `NoStoreys` [keep→direct/high] → **preserve**
  - Lisbon: `Nr de pisos` [keep→direct/high] → **preserve**
  - Paris: `nombre_niveau_logement` [keep→partial/medium] → **preserve**

### `house_number` — 🟠 conditional
_House numbers are generally comparable text values, but formatting, suffix handling, and leading-zero/normalization conventions differ. The canonical concept is only the primary number, so cities mixing suffixes would need parsing outside the provided rules._
  - Amsterdam: `Huisnummer` [keep→direct/high] → **preserve**
  - Barcelona: `numero` [keep→direct/high] → **preserve**
  - Copenhagen: `HouseNumber` [keep→direct/high] → **preserve**
  - Paris: `numero_voie_ban` [keep→direct/high] → **preserve**
  - Turin: `numero_civico` [keep→direct/high] → **preserve**

### `latitude` — 🟠 conditional
_Latitude values are comparable only if all fields are in geographic degrees in a standard geographic CRS. Copenhagen explicitly states WGS84; Barcelona and Turin appear geographic but CRS is not explicitly stated here._
  - Barcelona: `latitud` [keep→direct/high] → **preserve**
  - Copenhagen: `Wgs84Latitude` [keep→direct/high] → **preserve**
  - Turin: `latitudine` [keep→direct/high] → **preserve**

### `longitude` — 🟠 conditional
_Longitude values are comparable only if all fields are in geographic degrees in a standard geographic CRS. Copenhagen explicitly states WGS84; Barcelona and Turin appear geographic but CRS is not explicitly stated here._
  - Barcelona: `longitud` [keep→direct/high] → **preserve**
  - Copenhagen: `Wgs84Longitude` [keep→direct/high] → **preserve**
  - Turin: `longitudine` [keep→direct/high] → **preserve**

### `municipality` — 🟠 conditional
_The fields all point to a surrounding administrative/locality area, but some cities provide postal towns, counties, subdivisions, or mixed locality strings rather than a formal municipality. Values are usable with caution and not perfectly harmonized administratively._
  - Barcelona: `poblacio` [keep→direct/high] → **preserve**
  - Copenhagen: `PostalCity` [downgrade→partial/medium] → **preserve**
  - Dublin: `CountyName` [downgrade→partial/medium] → **preserve**
  - Liège: `city` [downgrade→partial/medium] → **preserve**
  - Lisbon: `Concelho` [keep→direct/high] → **preserve**
  - London: `posttown` [downgrade→partial/medium] → **preserve**
  - Madrid: `edif_muni` [keep→direct/high] → **preserve**
  - Paris: `nom_commune_ban` [keep→direct/high] → **preserve**

### `street_name` — 🟠 conditional
_Street names are a standard address component, but cross-city values vary by language, abbreviations, spelling conventions, and geocoded normalization practices. They are comparable as text labels, not as harmonized categories._
  - Barcelona: `adre_a` [flag→uncertain/low] → **preserve**
  - Copenhagen: `StreetName` [keep→direct/high] → **preserve**
  - Paris: `nom_rue_ban` [keep→direct/high] → **preserve**

### `certificate_context` — 🟠 conditional
_All cities capture some administrative or application context, but the value spaces differ materially: some encode building lifecycle/certificate state (existing, new, provisional, completed), while others encode transaction or asset-scope context. Values can only be compared after coarse grouping, and even then only for limited shared distinctions such as existing vs new/project/finalized._
  - Amsterdam: `Status` [keep→direct/high] → **preserve**
  - Barcelona: `tipus_tramit` [keep→direct/high] → **preserve**
  - Dublin: `TypeofRating` [keep→direct/high] → **preserve**
  - London: `transaction_type` [downgrade→partial/medium] → **preserve**
  - Madrid: `edif_tiporeg` [keep→direct/high] → **preserve**
  - Paris: `methode_application_dpe` [downgrade→partial/medium] → **preserve**

### `domestic_hot_water_cost_total` — 🟠 conditional
_Both cities appear to report annual DHW cost, but currency units, tax treatment, tariff assumptions, and reference price year may differ. Values are only conditionally comparable, mainly within-country or after explicit price/currency normalization not available here._
  - London: `hot_water_cost_current` [keep→direct/high] → **preserve**
  - Paris: `cout_ecs` [keep→direct/high] → **preserve**

### `heating_cost_total` — 🟠 conditional
_Where correctly identified, values represent annual heating-related cost, but cross-city comparability is limited by different currencies, taxes, and tariff assumptions. One city cell is definitionally uncertain, and Copenhagen's label is ambiguous between supply charge and modeled end-use cost._
  - Copenhagen: `HeatSupplyCost` [downgrade→partial/medium] → **preserve**
  - London: `heating_cost_current` [keep→direct/high] → **preserve**
  - Madrid: `edif_calef` [flag→uncertain/low] → **preserve**
  - Paris: `cout_chauffage` [keep→direct/high] → **preserve**

### `lighting_cost_total` — 🟠 conditional
_Both cities appear to report annual lighting cost, but currency and price assumptions may differ, so raw values are only conditionally comparable._
  - London: `lighting_cost_current` [keep→direct/high] → **preserve**
  - Paris: `cout_eclairage` [keep→direct/high] → **preserve**

### `total_energy_cost` — 🟠 conditional
_All mapped cells refer to annual total or near-total energy cost, but end-use coverage differs: Paris explicitly covers 5 uses, Barcelona is approximate total cost with unspecified coverage, and Madrid is a derived subtotal excluding lighting. Combined with cross-country pricing differences, values are only conditionally comparable._
  - Barcelona: `cost_anual_aproximat_d_energia` [keep→direct/medium] → **preserve**
  - Madrid: `edif_refrig,edif_calef,edif_acs` [downgrade→partial/low] → **derive** {'operation': 'sum', 'operands': ['edif_refrig', 'edif_calef', 'edif_acs']}
  - Paris: `cout_total_5_usages` [keep→direct/high] → **preserve**

### `cooling_system_type` — 🟠 conditional
_Both cities target cooling equipment type, but the concept note indicates sources may mix generator type with broader system configuration. Values are only comparable after harmonizing to broad classes (e.g., split/central/chiller/heat pump/none/other) and not at detailed taxonomy level._
  - Madrid: `refrig_tipo` [keep→direct/high] → **preserve**
  - Paris: `type_generateur_froid` [keep→direct/high] → **preserve**

### `heating_installation_scope` — 🟠 conditional
_Within this single-city concept, the reported typology is interpretable, but cross-registry distinctions between collective, community, and mixed are not perfectly standardized. Broad grouping is usually possible._
  - Paris: `type_installation_chauffage` [keep→direct/high] → **preserve**

### `heating_system_type` — 🟠 conditional
_All cities describe the main heating system/generator in some form, but fields vary between generator type, system description, and detailed national catalogs. Values are comparable only after recategorization to broad heating classes._
  - Liège: `device` [keep→direct/high] → **preserve**
  - London: `mainheat_description` [keep→partial/high] → **preserve**
  - Madrid: `calefac_tipo` [keep→direct/high] → **preserve**
  - Paris: `type_generateur_chauffage_principal` [keep→partial/medium] → **preserve**

### `hot_water_installation_scope` — 🟠 conditional
_Within this single-city concept, the installation-scope categories are interpretable, but broad grouping is safer than assuming exact equivalence with other registries' collective/community/mixed definitions._
  - Paris: `type_installation_ecs` [keep→direct/high] → **preserve**

### `hot_water_system_type` — 🟠 conditional
_Cities report DHW system/generator information, but some fields are descriptions and others are detailed generator taxonomies. Values are comparable only after harmonization to broad DHW system classes._
  - London: `hotwater_description` [keep→partial/medium] → **preserve**
  - Madrid: `acs_tipo` [keep→direct/high] → **preserve**
  - Paris: `type_generateur_chauffage_principal_ecs` [keep→partial/medium] → **preserve**

### `low_energy_lighting_share` — 🟠 conditional
_Both cities report a share/percentage of low-energy lighting, which is broadly comparable, but the definition of what counts as low-energy may vary somewhat by methodology period and registry rules._
  - Dublin: `LowEnergyLightingPercent` [keep→direct/high] → **preserve**
  - London: `low_energy_lighting` [keep→direct/high] → **preserve**

### `main_heating_fuel` — 🟠 conditional
_All cities capture the primary heating energy carrier/fuel, but some fields blend fuel with supply mode or community/district distinctions. Broad carrier-level comparison is feasible after harmonization, while detailed distinctions may not be directly comparable._
  - Copenhagen: `HeatSupply` [downgrade→partial/medium] → **preserve**
  - Dublin: `MainSpaceHeatingFuel` [keep→direct/high] → **preserve**
  - London: `main_fuel` [downgrade→partial/medium] → **preserve**
  - Madrid: `calefac_vector` [keep→direct/high] → **preserve**
  - Paris: `type_energie_principale_chauffage` [keep→direct/high] → **preserve**

### `main_hot_water_fuel` — 🟠 conditional
_The cities report the primary DHW fuel/energy carrier, which is broadly comparable, but detailed distinctions may still vary between pure fuel and broader carrier labeling. Broad carrier-level harmonization is appropriate._
  - Dublin: `MainWaterHeatingFuel` [keep→direct/high] → **preserve**
  - Madrid: `acs_vector` [keep→direct/high] → **preserve**
  - Paris: `type_energie_principale_ecs` [keep→direct/high] → **preserve**

### `solar_hot_water_present` — 🟠 conditional
_Boolean presence of solar thermal related to hot water is broadly comparable, but some cities capture any solar thermal contribution and others infer presence from installation type or allow contribution to multiple end uses including heating. Presence is comparable with scope caveats._
  - Barcelona: `solar_termica` [keep→direct/high] → **preserve**
  - Dublin: `SolarHotWaterHeating` [keep→direct/high] → **preserve**
  - London: `solar_water_heating_flag` [keep→direct/high] → **preserve**
  - Madrid: `termica_nombre` [keep→partial/medium] → **preserve**
  - Paris: `type_installation_solaire_n1` [keep→partial/medium] → **preserve**

### `solar_pv_present` — 🟠 conditional
_On-site PV presence is broadly comparable as a boolean, but some cities infer presence indirectly from secondary energy descriptions or nonzero supply values rather than an explicit presence flag. Cross-city use is acceptable with caution for inferred cases._
  - Barcelona: `solar_fotovoltaica` [keep→direct/high] → **preserve**
  - Dublin: `SecondEnergyType_Description` [flag→uncertain/low] → **preserve**
  - London: `photo_supply` [keep→partial/medium] → **preserve**
  - Madrid: `elec_nombre` [keep→direct/high] → **preserve**
  - Paris: `presence_production_pv` [keep→direct/high] → **preserve**

### `ventilation_type` — 🟠 conditional
_Ventilation method/system type is conceptually comparable at broad level, but national taxonomies differ and two city mappings are uncertain. Cross-city comparison should be limited to broad categories such as natural, mechanical exhaust, balanced/mechanical supply-extract, mixed, or unknown._
  - Barcelona: `ventilacio_us_residencial` [flag→uncertain/low] → **preserve**
  - Dublin: `VentilationMethod` [keep→direct/high] → **preserve**
  - London: `mechanical_ventilation` [downgrade→partial/medium] → **preserve**
  - Madrid: `edif_acrist` [flag→uncertain/low] → **preserve**
  - Paris: `type_ventilation` [keep→direct/high] → **preserve**

### `assessment_date` — 🟠 conditional
_Most cities provide the assessment/inspection/visit date and these values are generally comparable as dates. However, Liège is ambiguous because the mapped field may represent certificate issuance or registration rather than the on-site assessment date, so cross-city comparability is conditional on excluding or separately treating that city._
  - Amsterdam: `Opnamedatum` [keep→direct/high] → **preserve**
  - Dublin: `DateOfAssessment` [keep→direct/high] → **preserve**
  - Liège: `certificate_date` [flag→uncertain/low] → **preserve**
  - London: `inspection_date` [keep→direct/high] → **preserve**
  - Paris: `date_visite_diagnostiqueur` [keep→direct/high] → **preserve**

### `certificate_valid_until` — 🟠 conditional
_Validity end dates are comparable as dates for Amsterdam, Copenhagen, and Paris. Turin is ambiguous because the mapped field appears to be a start/effective date rather than a validity end date, so cross-city comparability is conditional on excluding Turin unless confirmed._
  - Amsterdam: `GeldigTot` [keep→direct/high] → **preserve**
  - Copenhagen: `ValidTo` [keep→direct/high] → **preserve**
  - Paris: `date_fin_validite_dpe` [keep→direct/high] → **preserve**
  - Turin: `data_decorrenza` [flag→uncertain/low] → **preserve**

### `construction_year` — 🟠 conditional
_Exact construction year is generally comparable across cities, but quality issues remain: some sources may contain invalid placeholders/outliers, and Turin is ambiguous because epoca_costruzione may be an age epoch/period rather than exact year. Comparability is therefore conditional on validation and excluding ambiguous/non-year values._
  - Amsterdam: `Bouwjaar` [keep→direct/high] → **preserve**
  - Barcelona: `any_construccio` [keep→direct/high] → **preserve**
  - Copenhagen: `YearOfConstruction` [keep→direct/high] → **preserve**
  - Dublin: `Year_of_Construction` [keep→direct/high] → **preserve**
  - Liège: `build_year` [keep→direct/high] → **preserve**
  - Madrid: `edif_año` [downgrade→partial/medium] → **preserve**
  - Paris: `annee_construction` [keep→direct/high] → **preserve**
  - Turin: `epoca_costruzione` [flag→uncertain/low] → **preserve**

### `registration_date` — 🟠 conditional
_Administrative dates for registration, lodgement, entry, establishment, or valid-from are related but not identical across systems. Some cities provide true registry/lodgement dates, while others provide establishment or validity-start dates. Cross-city use is possible only with caution and awareness that these dates may differ systematically by process stage._
  - Amsterdam: `Registratiedatum` [keep→direct/high] → **preserve**
  - Barcelona: `data_entrada` [keep→direct/high] → **preserve**
  - Copenhagen: `ValidFrom` [downgrade→partial/medium] → **preserve**
  - London: `lodgement_date` [keep→direct/high] → **preserve**
  - Madrid: `edif_fecha` [flag→uncertain/low] → **preserve**
  - Paris: `date_etablissement_dpe` [keep→partial/medium] → **preserve**

### `cooling_demand_total` — 🔴 not_comparable
_Only one city is present, so cross-city value comparability cannot be established. In addition, cooling demand totals are methodology-dependent and high risk._
  - Paris: `besoin_refroidissement` [keep→direct/high] → **preserve**

### `delivered_energy_total` — 🔴 not_comparable
_Only one city is present, so there is no cross-city basis for comparison. Delivered energy methodology also varies and is distinct from final or primary energy._
  - Dublin: `TotalDeliveredEnergy` [keep→direct/high] → **preserve**

### `domestic_hot_water_demand_total` — 🔴 not_comparable
_Only one city is present, so cross-city value comparability cannot be established. DHW demand totals are also methodology-dependent._
  - Paris: `besoin_ecs` [keep→direct/high] → **preserve**

### `final_energy_total` — 🔴 not_comparable
_Paris reports a 5-use final energy total, while Copenhagen's source is unspecified total consumption with unclear energy basis and scope. Because both semantic certainty and methodological scope differ substantially, values are not comparable across cities._
  - Copenhagen: `CalculatedConsumption` [flag→uncertain/low] → **preserve**
  - Paris: `conso_5_usages_ef` [keep→direct/high] → **preserve**

### `primary_energy_intensity` — 🔴 not_comparable
_The cities do not report equivalent primary energy bases: Barcelona and Turin are explicitly non-renewable primary energy intensity, Paris is 5-use primary energy intensity, and Liège is a specific EPC performance indicator with unclear exact energy scope and area basis. These are related but not value-comparable as a single metric._
  - Barcelona: `energia_prim_ria_no_renovable` [keep→partial/medium] → **preserve**
  - Liège: `e_spec` [flag→uncertain/low] → **preserve**
  - Paris: `conso_5_usages_par_m2_ep` [keep→direct/high] → **preserve**
  - Turin: `epglnren_ape` [downgrade→partial/high] → **preserve**

### `primary_energy_total` — 🔴 not_comparable
_Paris reports 5-use primary energy total, Madrid may be non-renewable primary and may even be intensity rather than total, and Copenhagen's scope is unspecified. Because basis, scope, and even dimensionality are inconsistent, values are not comparable._
  - Copenhagen: `CalculatedEnergyConsumption` [flag→uncertain/low] → **preserve**
  - Madrid: `norenov_global` [flag→uncertain/low] → **preserve**
  - Paris: `conso_5_usages_ep` [keep→direct/high] → **preserve**

### `space_heating_demand_total` — 🔴 not_comparable
_Only one city is present, so cross-city comparability cannot be established. Heating demand totals are also methodology-dependent._
  - Paris: `besoin_chauffage` [keep→direct/high] → **preserve**

### `building_compactness` — 🔴 not_comparable
_Values are not reliably comparable across cities because the concept is methodology-specific and the mapped fields do not share a confirmed common formula. Amsterdam and Madrid appear to be EPC-model compactness/form-factor indicators with unspecified definitions, while Paris is a different construct (global heat-loss coefficient per m²K / Ubât), not a geometric compactness ratio._
  - Amsterdam: `Compactheid` [keep→direct/medium] → **preserve**
  - Madrid: `edif_compac` [keep→direct/medium] → **preserve**
  - Paris: `ubat_w_par_m2_k` [flag→uncertain/low] → **preserve**

### `climate_zone` — 🔴 not_comparable
_Climate-zone codes are EPC-methodology-specific national taxonomies. Even where all cells are semantically direct, the category values are not aligned across Spain, Portugal, and France, and Lisbon is winter-only scope._
  - Barcelona: `zona_climatica` [keep→direct/high] → **preserve**
  - Lisbon: `Zona Climática de Inverno` [downgrade→partial/medium] → **preserve**
  - Madrid: `edif_zonaclim` [keep→direct/high] → **preserve**
  - Paris: `zone_climatique` [keep→direct/high] → **preserve**

### `projected_x_coordinate` — 🔴 not_comparable
_Projected easting values are not directly comparable across cities because the CRS differs or is unstated. Numeric values in different projected systems cannot be aligned without explicit CRS metadata and reprojection._
  - Barcelona: `utm_x` [keep→direct/high] → **preserve**
  - Paris: `coordonnee_cartographique_x_ban` [downgrade→partial/medium] → **preserve**

### `projected_y_coordinate` — 🔴 not_comparable
_Projected northing values are not directly comparable across cities because the CRS differs or is unstated. Numeric values in different projected systems cannot be aligned without explicit CRS metadata and reprojection._
  - Barcelona: `utm_y` [keep→direct/high] → **preserve**
  - Paris: `coordonnee_cartographique_y_ban` [downgrade→partial/medium] → **preserve**

### `certification_method` — 🔴 not_comparable
_Although each city records methodology/tool information, the values are registry-specific software names, versions, or national methods. These do not form a common comparable scale across countries._
  - Amsterdam: `Berekeningstype` [keep→direct/high] → **preserve**
  - Barcelona: `eina_de_certificacio` [keep→direct/high] → **preserve**
  - Madrid: `edif_proced` [keep→direct/high] → **preserve**
  - Paris: `modele_dpe` [keep→direct/high] → **preserve**

### `co2_class` — 🔴 not_comparable
_All mapped cells are semantically about CO2/GHG emissions class labels, but the class scales, thresholds, and emissions scope are country-specific. Identical letters are not guaranteed to represent equivalent emissions performance across Spain and France, so values should not be normalized or compared directly across cities._
  - Barcelona: `qualificacio_d_emissions` [keep→direct/high] → **preserve**
  - Madrid: `cal_co2global` [keep→direct/high] → **preserve**
  - Paris: `etiquette_ges` [keep→direct/high] → **preserve**

### `energy_class` — 🔴 not_comparable
_Every city cell is a direct semantic match to a national EPC energy label/class, but the labels are assigned under different national methodologies, metrics, thresholds, and sometimes subclass systems. Therefore class values are not directly comparable across cities, even when they share letters like A-G._
  - Amsterdam: `Energieklasse` [keep→direct/high] → **preserve**
  - Barcelona: `qualificaci_de_consum_d` [keep→direct/high] → **preserve**
  - Copenhagen: `CurrentEnergyLabelClassification` [keep→direct/high] → **preserve**
  - Dublin: `EnergyRating` [keep→direct/high] → **preserve**
  - Liège: `e_spec_label` [keep→direct/high] → **preserve**
  - London: `current_energy_rating` [keep→direct/high] → **preserve**
  - Madrid: `cal_norenovglobal` [downgrade→partial/medium] → **preserve**
  - Paris: `etiquette_dpe` [keep→direct/high] → **preserve**
  - Turin: `classe_energetica_ape` [keep→direct/high] → **preserve**

### `energy_efficiency_score` — 🔴 not_comparable
_The mapped values are all numeric rating scores, but they are defined by different national systems and do not share a common metric or scale. Dutch Energie-Index, Irish BER numeric rating, UK SAP/EPC score, and Italian composite APE score cannot be treated as directly comparable quantities._
  - Amsterdam: `EnergieIndex` [keep→direct/high] → **preserve**
  - Dublin: `BerRating` [downgrade→partial/medium] → **preserve**
  - London: `current_energy_efficiency` [keep→direct/high] → **preserve**
  - Turin: `ape_score_total` [downgrade→partial/low] → **preserve**

### `heating_system_efficiency` — 🔴 not_comparable
_Reported heating efficiency values are not reliably comparable across cities because the underlying metric basis can differ materially: seasonal/system efficiency, generator efficiency, or COP-like performance, and some values may exceed 100 for heat pumps or include sentinel/outlier values. This affects the meaning of the numeric values, not just units._
  - Dublin: `HSMainSystemEfficiency` [downgrade→partial/medium] → **preserve**
  - Madrid: `calefac_rendim` [keep→partial/medium] → **preserve**

### `hot_water_system_efficiency` — 🔴 not_comparable
_Reported DHW efficiency/performance values are not reliably comparable across cities because the underlying basis may differ by methodology or technology, and placeholder/sentinel values may exist. The numbers may share a percent-like format while representing different constructs._
  - Dublin: `WHMainSystemEff` [downgrade→partial/medium] → **preserve**
  - Madrid: `acs_rendim` [keep→partial/medium] → **preserve**

### `construction_period` — 🔴 not_comparable
_All cities use country- or policy-specific construction period categories with different bin edges, naming schemes, and in Barcelona a regulatory-era proxy rather than pure age bands. Values should not be compared as identical categories across cities without an explicit crosswalk._
  - Barcelona: `normativa_construcci` [keep→partial/medium] → **preserve**
  - Liège: `build_period_v2` [keep→direct/high] → **preserve**
  - Lisbon: `Período_Construcao_Geral` [keep→direct/high] → **preserve**
  - London: `construction_age_band` [keep→direct/high] → **preserve**
  - Paris: `periode_construction` [keep→direct/high] → **preserve**