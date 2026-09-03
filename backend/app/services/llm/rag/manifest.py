"""
Ingestion manifest — which documents to ingest per city slice.

Values are taken verbatim from the corpus "National Inventory" / "Ingest First"
sheets (``backend/docs/knowledge/corpus/*.xlsx``). The Turin slice starts with
the 3 national documents on the "Ingest First" shortlist; Piemonte/Torino
regional + municipal tiers are added here later (Phase D) to activate the
cascade — no code change, just new manifest rows.
"""

from __future__ import annotations

from typing import List, TypedDict

from app.services.llm.rag.metadata import DocType, JurisdictionLevel, UseCase


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


# Ingest First (national shortlist) — Italy / Turin slice
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
]

MANIFESTS = {"turin": TURIN}
