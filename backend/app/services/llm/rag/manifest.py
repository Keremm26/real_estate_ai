"""
Ingestion manifest — which documents to ingest per city slice.

Values are taken verbatim from the corpus "National Inventory" / "Ingest First"
sheets (``backend/docs/knowledge/corpus/*.xlsx``). Each city slice carries the
full national -> regional -> municipal cascade; adding a tier is a manifest row,
never a code change.
"""

from __future__ import annotations

from typing import List, NotRequired, TypedDict

from app.services.llm.rag.metadata import DocType, JurisdictionLevel, UseCase


class DocSpan(TypedDict):
    """Restrict ingestion to one part of a source — the heading that opens it
    and the heading that follows it, as multiline regexes each matching the
    cleaned text exactly once. Used when a regulation bundles the relevant
    title with hundreds of unrelated articles (food hygiene, mortuary police)
    that would only dilute the retrieval pool. The raw cache keeps the whole
    document; the span is applied at chunk time."""
    start: str
    end: str


class DocSpec(TypedDict):
    doc_key: str              # stable cache/id slug
    doc_name: str             # official title (as in the inventory)
    url: str                  # official source — cited as source_url in metadata
    fetch_url: str            # where the clean article text is actually pulled from
    country: str
    jurisdiction_level: JurisdictionLevel
    doc_type: DocType
    use_case: UseCase
    lang: str
    effective_date: str
    span: NotRequired[DocSpan]  # ingest only this part of the source


# --------------------------------------------------------------------------
# Turin slice — national -> Piemonte -> Città di Torino. Italy's student
# room-size rule sits at the NATIONAL tier (DM Sanità 1975 / DM 1256/2021) but
# the operative use category ("residenze collettive per studenti") is MUNICIPAL
# (NUEA Art. 3), so the cascade must reach the city tier to classify a project.
# NOT INCLUDED (corpus rows 16, 18): L.R. 56/1977 (350k chars of plan-procedure
# law with few queryable thresholds — same distractor profile as Madrid's
# Ley 9/2001) and the RET Piemonte (definitions only). Both fetch cleanly from
# arianna.cr.piemonte.it if wanted. Rows 20-24, 29-30 (energy, noise, seismic,
# landscape, healthcare) are procedural/map-based and out of the student use case.
# --------------------------------------------------------------------------
TURIN: List[DocSpec] = [
    {
        "doc_key": "dpr_380_2001",
        "doc_name": "D.P.R. 6 giugno 2001, n. 380 — Testo unico dell'edilizia",
        # official (normattiva) is JS-rendered; pull clean article HTML from mirror
        "url": "https://www.normattiva.it/eli/stato/DECRETO_DEL_PRESIDENTE_DELLA_REPUBBLICA/2001/06/06/380/CONSOLIDATED",
        "fetch_url": "https://www.bosettiegatti.eu/info/norme/statali/2001_0380.htm",
        "country": "IT",
        "jurisdiction_level": JurisdictionLevel.NATIONAL,
        "doc_type": DocType.BUILDING,
        "use_case": UseCase.GENERAL,
        "lang": "it",
        "effective_date": "2002-01-01",
    },
    {
        "doc_key": "dm_sanita_1975",
        "doc_name": "D.M. Sanità 5 luglio 1975 — Requisiti igienico-sanitari dei locali di abitazione",
        # official (gazzettaufficiale) is PDF-only; pull clean article HTML from mirror
        "url": "https://www.gazzettaufficiale.it/eli/gu/1975/07/18/190/sg/pdf",
        "fetch_url": "https://www.bosettiegatti.eu/info/norme/statali/1975_dm_05_07.htm",
        "country": "IT",
        "jurisdiction_level": JurisdictionLevel.NATIONAL,
        "doc_type": DocType.BUILDING,
        "use_case": UseCase.GENERAL,
        "lang": "it",
        "effective_date": "1975-07-18",
    },
    {
        "doc_key": "dm_236_1989",
        "doc_name": "D.M. Lavori Pubblici 14 giugno 1989, n. 236 — Prescrizioni tecniche (accessibilità)",
        # official (gazzettaufficiale) serves a menu frame; pull clean article HTML from mirror
        "url": "https://www.gazzettaufficiale.it/atto/vediMenuHTML?atto.codiceRedazionale=089G0298&atto.dataPubblicazioneGazzetta=1989-06-23&tipoSerie=serie_generale&tipoVigenza=originario",
        "fetch_url": "https://www.bosettiegatti.eu/info/norme/statali/1989_0236.htm",
        "country": "IT",
        "jurisdiction_level": JurisdictionLevel.NATIONAL,
        "doc_type": DocType.ACCESSIBILITY,
        "use_case": UseCase.GENERAL,
        "lang": "it",
        "effective_date": "1989-06-23",
    },
    {
        "doc_key": "dm_1444_1968",
        "doc_name": "D.M. Lavori Pubblici 2 aprile 1968, n. 1444 — Limiti inderogabili di densità, altezza e distanza fra i fabbricati",
        "url": "https://www.gazzettaufficiale.it/atto/serie_generale/caricaDettaglioAtto/originario?atto.codiceRedazionale=1288Q004&atto.dataPubblicazioneGazzetta=1968-04-16&elenco30giorni=false",
        # official (gazzettaufficiale) is a menu frame; pull clean article HTML from mirror
        "fetch_url": "https://www.bosettiegatti.eu/info/norme/statali/1968_1444.htm",
        "country": "IT",
        "jurisdiction_level": JurisdictionLevel.NATIONAL,
        "doc_type": DocType.PLANNING,
        "use_case": UseCase.GENERAL,
        "lang": "it",
        "effective_date": "1968-04-16",
    },
    {
        "doc_key": "legge_122_1989",
        "doc_name": "Legge 24 marzo 1989, n. 122 — Disposizioni in materia di parcheggi (Tognoli)",
        # official (normattiva) is JS-rendered; pull clean article HTML from mirror
        "url": "https://www.normattiva.it/eli/stato/LEGGE/1989/03/24/122/CONSOLIDATED",
        "fetch_url": "https://www.bosettiegatti.eu/info/norme/statali/1989_0122.htm",
        "country": "IT",
        "jurisdiction_level": JurisdictionLevel.NATIONAL,
        "doc_type": DocType.PLANNING,
        "use_case": UseCase.GENERAL,
        "lang": "it",
        "effective_date": "1989-04-07",
    },
    # --- student-housing-specific national standard (PDF-only; annex holds the
    #     dimensional rules). Tagged student_housing so the use-case cascade
    #     surfaces it for student queries while staying invisible to general ones.
    {
        "doc_key": "dm_1256_2021",
        "doc_name": "D.M. MUR 30 novembre 2021, n. 1256 — Standard minimi per alloggi e residenze universitarie",
        "url": "https://www.mur.gov.it/it/atti-e-normativa/decreto-ministeriale-n-1256-del-30-11-2021",
        "fetch_url": "https://www.mur.gov.it/sites/default/files/2022-01/DM%20n.%201256%20del%2030-11-2021.pdf",
        "country": "IT",
        "jurisdiction_level": JurisdictionLevel.NATIONAL,
        "doc_type": DocType.BUILDING,
        "use_case": UseCase.STUDENT_HOUSING,
        "lang": "it",
        "effective_date": "2022-02-16",
    },
    {
        "doc_key": "dm_1256_2021_allegato_a",
        "doc_name": "D.M. MUR 1256/2021 — Allegato A (standard dimensionali e qualitativi)",
        "url": "https://www.mur.gov.it/it/atti-e-normativa/decreto-ministeriale-n-1256-del-30-11-2021",
        "fetch_url": "https://www.mur.gov.it/sites/default/files/2022-01/DM%20n.%201256%20Allegato%20A.pdf",
        "country": "IT",
        "jurisdiction_level": JurisdictionLevel.NATIONAL,
        "doc_type": DocType.BUILDING,
        "use_case": UseCase.STUDENT_HOUSING,
        "lang": "it",
        "effective_date": "2022-02-16",
    },
    # --- Piemonte regional tier. arianna.cr.piemonte.it serves the official
    #     coordinated text server-side (no JS), so url == fetch_url.
    {
        "doc_key": "lr_piemonte_19_1999",
        "doc_name": "L.R. Piemonte 8 luglio 1999, n. 19 — Norme in materia edilizia e modifiche alla L.R. 56/1977",
        "url": "https://arianna.cr.piemonte.it/iterlegcoordweb/dettaglioLegge.do?urnLegge=urn%3Anir%3Aregione.piemonte%3Alegge%3A1999%3B19",
        "fetch_url": "https://arianna.cr.piemonte.it/iterlegcoordweb/dettaglioLegge.do?urnLegge=urn%3Anir%3Aregione.piemonte%3Alegge%3A1999%3B19",
        "country": "IT",
        "jurisdiction_level": JurisdictionLevel.REGIONAL,
        "doc_type": DocType.BUILDING,
        "use_case": UseCase.GENERAL,
        "lang": "it",
        "effective_date": "1999-07-14",
    },
    {
        "doc_key": "lr_piemonte_16_2018",
        "doc_name": "L.R. Piemonte 4 ottobre 2018, n. 16 — Misure per il riuso, la riqualificazione dell'edificato e la rigenerazione urbana",
        "url": "https://arianna.cr.piemonte.it/iterlegcoordweb/dettaglioLegge.do?urnLegge=urn%3Anir%3Aregione.piemonte%3Alegge%3A2018%3B16",
        "fetch_url": "https://arianna.cr.piemonte.it/iterlegcoordweb/dettaglioLegge.do?urnLegge=urn%3Anir%3Aregione.piemonte%3Alegge%3A2018%3B16",
        "country": "IT",
        "jurisdiction_level": JurisdictionLevel.REGIONAL,
        "doc_type": DocType.BUILDING,
        "use_case": UseCase.GENERAL,
        "lang": "it",
        "effective_date": "2018-10-11",
    },
    # --- Città di Torino municipal tier. The PRG is under a safeguard regime
    #     (preliminary revision adopted 16 Mar 2026, definitive technical proposal
    #     1 Sep 2026; art. 58 L.R. 56/1977, max 36 months) — the 1995 NUEA
    #     remain the text in force. comune.torino.it serves the regulation PDFs
    #     from extensionless /media/NNNN attachment links.
    {
        "doc_key": "to_prg_nuea_vol1",
        "doc_name": "PRG Torino 1995 — Norme Urbanistico Edilizie di Attuazione, Volume I (testo coordinato al 31 dicembre 2025)",
        "url": "https://www.comune.torino.it/schede-informative/piano-regolatore-generale-prg",
        "fetch_url": "http://geoportale.comune.torino.it/web/sites/default/files/mediafiles/volume_i_15.pdf",
        "country": "IT",
        "jurisdiction_level": JurisdictionLevel.MUNICIPAL,
        "doc_type": DocType.PLANNING,
        "use_case": UseCase.GENERAL,
        "lang": "it",
        "effective_date": "2025-12-31",
    },
    {
        "doc_key": "to_reg_381_edilizio",
        "doc_name": "Regolamento comunale n. 381 — Regolamento Edilizio della Città di Torino",
        "url": "https://www.comune.torino.it/amministrazione/documenti-dati/documenti/n-381-regolamento-edilizio",
        "fetch_url": "https://www.comune.torino.it/media/8251",
        "country": "IT",
        "jurisdiction_level": JurisdictionLevel.MUNICIPAL,
        "doc_type": DocType.BUILDING,
        "use_case": UseCase.GENERAL,
        "lang": "it",
        "effective_date": "2023-10-16",
    },
    # Hygiene regulation, Titolo III only: dwelling habitability plus the
    # lodging-house/dormitory provisions (30 m³ air per person, WC ratios) that
    # are the closest Italian analogue to England's HMO conditions — tagged
    # BUILDING as the text is habitability, not licensing. The other six titles
    # (food, infectious disease, mortuary police, heating plants, school medical
    # service — ~480 articles) are out of scope and would swamp the IT pool.
    {
        "doc_key": "to_reg_30_igiene",
        "doc_name": "Regolamento comunale n. 30 — Regolamento d'Igiene della Città di Torino, Titolo III (Igiene del suolo e dell'abitato)",
        "url": "https://www.comune.torino.it/amministrazione/documenti-dati/documenti/n-30-regolamento-digiene",
        "fetch_url": "https://www.comune.torino.it/media/2896",
        "span": {
            "start": r"^TITOLO III - IGIENE DEL SUOLO E DELL'ABITATO$",
            "end": r"^TITOLO IV - IGIENE DEGLI ALIMENTI, DELLE BEVANDE, DEGLI$",
        },
        "country": "IT",
        "jurisdiction_level": JurisdictionLevel.MUNICIPAL,
        "doc_type": DocType.BUILDING,
        "use_case": UseCase.GENERAL,
        "lang": "it",
        "effective_date": "2018-02-12",
    },
]

# --------------------------------------------------------------------------
# London / Tower Hamlets slice (Phase 1 — the decisive PBSA-conversion rules).
# England splits into three regimes that do NOT substitute for each other, so
# doc_type carries real weight here: PLANNING decides whether the use is lawful,
# while HOUSING (HMO licensing) is where the student room-size rule actually
# lives — a different tier from Italy (national) and Madrid (municipal).
# NOT INCLUDED: Tower Hamlets Private Rental Accommodation & Amenity Standards
# (the local 8.5 m² override) — democracy.towerhamlets.gov.uk returns 403 to
# automated fetch; save the PDF manually into the raw cache to add it.
# DELIBERATELY EXCLUDED: NDSS and Housing Design Standards LPG — both are C3-only
# and expressly do not apply to PBSA; ingesting them would inject wrong room
# sizes (e.g. 37 m²) into student queries.
# --------------------------------------------------------------------------
LONDON: List[DocSpec] = [
    {
        "doc_key": "use_classes_1987",
        "doc_name": "The Town and Country Planning (Use Classes) Order 1987 (SI 1987/764)",
        "url": "https://www.legislation.gov.uk/uksi/1987/764/contents",
        "fetch_url": "https://www.legislation.gov.uk/uksi/1987/764",
        "country": "UK",
        "jurisdiction_level": JurisdictionLevel.NATIONAL,
        "doc_type": DocType.PLANNING,
        "use_case": UseCase.GENERAL,
        "lang": "en",
        "effective_date": "1987-06-01",
    },
    {
        "doc_key": "gpdo_2015_part3",
        "doc_name": "T&CP (General Permitted Development) (England) Order 2015 — Sch. 2 Part 3 (changes of use)",
        "url": "https://www.legislation.gov.uk/uksi/2015/596/contents",
        "fetch_url": "https://www.legislation.gov.uk/uksi/2015/596/schedule/2/part/3",
        "country": "UK",
        "jurisdiction_level": JurisdictionLevel.NATIONAL,
        "doc_type": DocType.PLANNING,
        "use_case": UseCase.GENERAL,
        "lang": "en",
        "effective_date": "2015-04-15",
    },
    {
        "doc_key": "hmo_mandatory_conditions_2018",
        "doc_name": "Licensing of HMOs (Mandatory Conditions of Licences) (England) Regulations 2018 (SI 2018/616)",
        "url": "https://www.legislation.gov.uk/uksi/2018/616/contents",
        "fetch_url": "https://www.legislation.gov.uk/uksi/2018/616",
        "country": "UK",
        "jurisdiction_level": JurisdictionLevel.NATIONAL,
        "doc_type": DocType.HOUSING,
        "use_case": UseCase.GENERAL,
        "lang": "en",
        "effective_date": "2018-10-01",
    },
    {
        "doc_key": "london_plan_2021",
        "doc_name": "The London Plan 2021 — Spatial Development Strategy for Greater London (Policy H15: PBSA)",
        "url": "https://www.london.gov.uk/programmes-strategies/planning/london-plan",
        # HTML chapters return 403 to automated fetch; the official PDF does not
        "fetch_url": "https://www.london.gov.uk/sites/default/files/the_london_plan_2021.pdf",
        "country": "UK",
        "jurisdiction_level": JurisdictionLevel.REGIONAL,
        "doc_type": DocType.PLANNING,
        "use_case": UseCase.GENERAL,
        "lang": "en",
        "effective_date": "2021-03-02",
    },
    {
        "doc_key": "th_local_plan_2031",
        "doc_name": "Tower Hamlets Local Plan 2031 — Managing Growth and Sharing Benefits (Policy D.H6: student housing)",
        "url": "https://www.towerhamlets.gov.uk/lgnl/planning_and_building_control/planning_policy_guidance/Local_plan/Local_Plan_2031_examination.aspx",
        "fetch_url": "https://www.towerhamlets.gov.uk/Documents/Planning-and-building-control/Strategic-Planning/Local-Plan/PoliciesPart1.pdf",
        "country": "UK",
        "jurisdiction_level": JurisdictionLevel.MUNICIPAL,
        "doc_type": DocType.PLANNING,
        "use_case": UseCase.GENERAL,
        "lang": "en",
        "effective_date": "2020-01-15",
    },
    {
        "doc_key": "th_article4_class_e",
        "doc_name": "Tower Hamlets Article 4 Direction — removal of Class MA permitted development (Class E to residential)",
        "url": "https://www.towerhamlets.gov.uk/lgnl/planning_and_building_control/planning_policy_guidance/Article_4_Directions.aspx",
        "fetch_url": "https://www.towerhamlets.gov.uk/Documents/Planning-and-building-control/Strategic-Planning/Modification-of-Article-4-Directions-DLUC.pdf",
        "country": "UK",
        "jurisdiction_level": JurisdictionLevel.MUNICIPAL,
        "doc_type": DocType.PLANNING,
        "use_case": UseCase.GENERAL,
        "lang": "en",
        "effective_date": "2022-08-18",
    },
]

# --------------------------------------------------------------------------
# Madrid slice (Phase 1 — full state -> regional -> municipal cascade).
# Spain keeps three clean tiers (unlike England's three parallel regimes), and
# the student rule is MUNICIPAL: the PGOUM classifies student housing as
# "residencia comunitaria" (establishment >= 40 m², one bedroom 12 m²).
# NOT INCLUDED: Ordenanza 6/2022 (licence / change-of-use pathway) —
# sede.madrid.es returns 403 to automated fetch on both the HTML and PDF ELI
# URLs; save it manually into the raw cache to add it.
# NOT INCLUDED: Decreto 111/2018 (abolishes the regional habitability
# certificate) — the URL in the corpus spreadsheet is WRONG: that BOCM item is
# "Ley 3/2018 ... contra la Violencia de Género". Needs a corrected source.
# DELIBERATELY EXCLUDED: Instrucción 2/2014 — ceased to have effect on
# 21 Mar 2024; ingesting it would inject obsolete student-residence criteria.
# --------------------------------------------------------------------------
MADRID: List[DocSpec] = [
    {
        "doc_key": "pgoum_nnuu_compendio",
        "doc_name": "PGOUM Madrid 1997 — Compendio de las Normas Urbanísticas (MPG NNUU, sept. 2025)",
        "url": "https://www.bocm.es/boletin/CM_Orden_BOCM/2023/11/14/BOCM-20231114-20.PDF",
        "fetch_url": "https://transparencia.madrid.es/UnidadesDescentralizadas/UDCUrbanismo/PGOUM/CompendioNNUU/Compendio_2025_septiembre/COMPENDIO_MPG_NNUU_24_09_2025.pdf",
        "country": "ES",
        "jurisdiction_level": JurisdictionLevel.MUNICIPAL,
        "doc_type": DocType.PLANNING,
        "use_case": UseCase.GENERAL,
        "lang": "es",
        "effective_date": "2025-09-24",
    },
    {
        "doc_key": "pgoum_preguntas_interes",
        "doc_name": "Ayuntamiento de Madrid — Preguntas de interés sobre la MPG NNUU (v5, sept. 2025)",
        "url": "https://www.madrid.es/UnidadesDescentralizadas/UrbanismoyVivienda/Urbanismo/UrbanismoNuevo/Planeamiento/DIFUSI%C3%93N_NN_UU/ficheros/Preguntas_inter%C3%A9s_MPG_NNUU_v5_septiembre_2025.pdf",
        "fetch_url": "https://www.madrid.es/UnidadesDescentralizadas/UrbanismoyVivienda/Urbanismo/UrbanismoNuevo/Planeamiento/DIFUSI%C3%93N_NN_UU/ficheros/Preguntas_inter%C3%A9s_MPG_NNUU_v5_septiembre_2025.pdf",
        "country": "ES",
        "jurisdiction_level": JurisdictionLevel.MUNICIPAL,
        "doc_type": DocType.PLANNING,
        "use_case": UseCase.GENERAL,
        "lang": "es",
        "effective_date": "2025-09-01",
    },
    {
        "doc_key": "ley_9_2001_suelo_madrid",
        "doc_name": "Ley 9/2001, de 17 de julio, del Suelo de la Comunidad de Madrid",
        "url": "https://www.boe.es/buscar/act.php?id=BOE-A-2001-18984",
        "fetch_url": "https://www.boe.es/buscar/act.php?id=BOE-A-2001-18984",
        "country": "ES",
        "jurisdiction_level": JurisdictionLevel.REGIONAL,
        "doc_type": DocType.PLANNING,
        "use_case": UseCase.GENERAL,
        "lang": "es",
        "effective_date": "2001-08-27",
    },
    {
        "doc_key": "cte_db_sua",
        "doc_name": "CTE Documento Básico DB-SUA — Seguridad de utilización y accesibilidad",
        "url": "https://www.codigotecnico.org/DocumentosCTE/SeguridadUtilizacionAccesibilidad.html",
        "fetch_url": "https://www.codigotecnico.org/pdf/Documentos/SUA/DBSUA.pdf",
        "country": "ES",
        "jurisdiction_level": JurisdictionLevel.NATIONAL,
        "doc_type": DocType.ACCESSIBILITY,
        "use_case": UseCase.GENERAL,
        "lang": "es",
        "effective_date": "2006-03-29",
    },
    {
        "doc_key": "cte_db_hs",
        "doc_name": "CTE Documento Básico DB-HS — Salubridad",
        "url": "https://www.codigotecnico.org/DocumentosCTE/Salubridad.html",
        "fetch_url": "https://www.codigotecnico.org/pdf/Documentos/HS/DccHS.pdf",
        "country": "ES",
        "jurisdiction_level": JurisdictionLevel.NATIONAL,
        "doc_type": DocType.BUILDING,
        "use_case": UseCase.GENERAL,
        "lang": "es",
        "effective_date": "2006-03-29",
    },
    {
        "doc_key": "cte_db_si",
        "doc_name": "CTE Documento Básico DB-SI — Seguridad en caso de incendio",
        "url": "https://www.codigotecnico.org/DocumentosCTE/SeguridadEnCasoDeIncendio.html",
        "fetch_url": "https://www.codigotecnico.org/pdf/Documentos/SI/DcmSI.pdf",
        "country": "ES",
        "jurisdiction_level": JurisdictionLevel.NATIONAL,
        "doc_type": DocType.BUILDING,
        "use_case": UseCase.GENERAL,
        "lang": "es",
        "effective_date": "2006-03-29",
    },
]

MANIFESTS = {"turin": TURIN, "london": LONDON, "madrid": MADRID}
