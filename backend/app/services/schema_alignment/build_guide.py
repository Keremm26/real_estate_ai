"""Rewrite 'Guide to read mapping.docx' to describe the updated, concept-anchored
workbook. Reuses the original document's own paragraph templates (title / body /
bullet / definition) so fonts, colours and bullet formatting are preserved.

Run:  cd backend && python3 -m app.services.schema_alignment.build_guide
"""
from __future__ import annotations

import copy
import shutil
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

DESKTOP = Path.home() / "Desktop"
DOC = DESKTOP / "Guide to read mapping.docx"
BACKUP = DESKTOP / "Guide to read mapping (old TIMEPAC version).docx"

# ---------------------------------------------------------------------------
# New content. Tuples: ("title"|"heading"|"body"|"bullet", text)
#                      ("def", label, body)
# ---------------------------------------------------------------------------
CONTENT = [
    ("title", "Guide to read the cross-country EPC mapping Excel"),
    ("body", "This workbook compares the available EPC / energy-performance datasets "
             "from ten European cities against a common schema of canonical concepts. "
             "The schema was built bottom-up: the concepts were discovered from the "
             "datasets themselves, and every city — including Turin — is mapped INTO "
             "the schema. The Italian APE / TIMEPAC feature list is kept only as a "
             "cross-reference column, not as the template the schema is built around."),
    ("body", "The purpose is to see, for each canonical concept, which column in each "
             "city's dataset corresponds to it, how well they align, and whether the "
             "concept is actually comparable across countries."),

    ("heading", "Cities covered"),
    ("body", "Ten cities are included: Amsterdam (Netherlands), Barcelona (Spain / "
             "Catalonia), Copenhagen (Denmark), Dublin (Ireland), Liège (Belgium), "
             "Lisbon (Portugal), London (United Kingdom), Madrid (Spain), Paris "
             "(France) and Turin (Italy). Turin, previously used only as the reference "
             "template, is now integrated as a full city that maps cleanly into the "
             "shared schema."),

    ("heading", "Structure of the file"),
    ("body", "The workbook contains three sheets:"),
    ("bullet", "Summary: an overview of each city dataset (format, number of rows and "
               "raw columns, how many concepts were mapped) plus Direct / Partial / "
               "Uncertain / Missing counts and the overall comparability statistics."),
    ("bullet", "Cross-country mapping: the main comparison table — one row per "
               "canonical concept."),
    ("bullet", "Common schema core: the concepts present in at least four cities — the "
               "strict cross-city backbone of the schema."),
    ("body", "Unlike the earlier version, this workbook does not include raw-sample "
             "sheets. Example values are shown inline in the notes, taken from redacted "
             "data profiles so that no personal or address-level data is exposed."),

    ("heading", "How to read the main mapping table"),
    ("body", "Each row corresponds to one canonical concept — a single, unit-defined "
             "piece of information. Examples of concepts:"),
    ("bullet", "energy performance class"),
    ("bullet", "primary / final energy intensity (kWh/m².year)"),
    ("bullet", "CO₂ emissions (total and intensity)"),
    ("bullet", "thermally conditioned floor area (m²)"),
    ("bullet", "construction year"),
    ("bullet", "wall / roof / window U-value"),
    ("bullet", "main heating fuel and system type"),
    ("body", "The first columns describe the concept itself:"),
    ("bullet", "Group – the thematic family (location, geometry, envelope, energy, "
               "emissions, systems, and so on)."),
    ("bullet", "Concept – the human label and its stable identifier."),
    ("bullet", "Definition and Canonical unit – what it means and the single unit it "
               "is expressed in."),
    ("bullet", "Comparability risk – low / medium / high: how safely the concept can "
               "be compared across countries."),
    ("bullet", "Comparability verdict – comparable / conditional / not_comparable: the "
               "reviewed conclusion for the concept across all cities."),
    ("bullet", "Cities mapped – in how many of the ten cities a source column was "
               "found."),
    ("bullet", "TIMEPAC requirement (cross-ref) and Italian APE XPath – the matching "
               "TIMEPAC feature and its XML location, shown only as a reference. Some "
               "concepts have no TIMEPAC counterpart (left blank), and some TIMEPAC "
               "features have no city data — this contrast is itself a finding."),
    ("body", "Then, for each of the ten cities, three columns report:"),
    ("bullet", "the source column name(s), if found;"),
    ("bullet", "the match type (Direct / Partial / Uncertain / Missing);"),
    ("bullet", "notes explaining the match, including a representative example value."),

    ("heading", "Meaning of the match types"),
    ("def", "Direct:", "The city column has the same or very similar meaning as the "
            "canonical concept. It can be compared with little or no transformation."),
    ("def", "Partial:", "The city dataset contains related information, but it is not a "
            "one-to-one match — a different unit, level of detail, aggregation, or "
            "scope. Useful for comparison, but it may need transformation or "
            "interpretation first."),
    ("def", "Uncertain:", "A related column probably exists, but its meaning, unit, or "
            "interpretation is not clear enough from the available data."),
    ("def", "Missing:", "No corresponding column was identified in that city's "
            "dataset."),

    ("heading", "Comparability risk and verdict"),
    ("body", "Two dimensions describe how trustworthy a cross-country comparison is, "
             "and they are kept separate from whether a column was found:"),
    ("def", "Comparability risk (low / medium / high):", "how sensitive the concept is "
            "to national methodology differences. Administrative fields such as "
            "postal_code or construction_year are low risk; energy_class, "
            "primary_energy_intensity and CO₂ figures are high risk because each "
            "country computes them differently."),
    ("def", "Comparability verdict (comparable / conditional / not_comparable):", "the "
            "reviewed conclusion for the concept. Most energy, rating and emissions "
            "concepts are not_comparable across countries even when every city reports "
            "them — this is the headline finding of the work."),

    ("heading", "How Turin fits in"),
    ("body", "Keep three levels in mind:"),
    ("bullet", "Canonical concept – the shared piece of information we compare."),
    ("bullet", "TIMEPAC requirement / Italian APE XPath – the cross-reference showing "
               "the matching Italian feature and where it lives in the APE XML."),
    ("bullet", "City source column – the actual column found in each city's dataset, "
               "including Turin's."),
    ("body", "Turin is now one of the ten mapped cities rather than the template. It "
             "maps cleanly into the bottom-up schema, which shows the schema "
             "accommodates Turin without being copied from it. In some cases a concept "
             "exists in the Italian APE XML but is not present in the extracted Turin "
             "tabular dataset; the notes point this out where relevant."),

    ("heading", "How to interpret the notes"),
    ("body", "The notes explain why a mapping is Direct, Partial, Uncertain, or "
             "Missing. When available they include:"),
    ("bullet", "the city column name and a representative example value (from the "
               "redacted profile);"),
    ("bullet", "the reason the match is direct or partial;"),
    ("bullet", "any qualifier (for example the floor-area basis or energy scope);"),
    ("bullet", "whether the reviewer downgraded or flagged the cell."),
    ("body", "Example values come from redacted data profiles (a typical value and "
             "range for numeric fields, the most frequent categories for text fields), "
             "never from raw individual records. Where no safe example was available, "
             "the note simply omits it."),

    ("heading", "City-level interpretation"),
    ("body", "Some datasets are already city-level, while others are national or "
             "regional and can be filtered to a city / municipality:"),
    ("bullet", "Turin: already city-specific."),
    ("bullet", "Madrid / Barcelona: Spanish EPC data, filterable by municipality."),
    ("bullet", "Paris (France): filterable by commune / postal code / INSEE code."),
    ("bullet", "London (UK): filterable by local authority or post town."),
    ("bullet", "Dublin (Ireland): filterable by county / small-area code."),
    ("bullet", "Amsterdam: filterable via address / postcode / BAG identifiers, though "
               "the sample has no explicit municipality column."),
    ("bullet", "Copenhagen (Denmark): city-level residential dataset."),
    ("bullet", "Liège (Belgium): regional (Wallonia) EPC data, filterable to the "
               "municipality."),
    ("bullet", "Lisbon: filterable by Concelho = Lisboa, but split across multiple "
               "files that may need joining by anonymized certificate ID."),

    ("heading", "Lisbon-specific note"),
    ("body", "The Lisbon / Portugal dataset is modular — information is spread across "
             "several files:"),
    ("bullet", "certificate / building details;"),
    ("bullet", "residential and non-residential data;"),
    ("bullet", "glazing information;"),
    ("bullet", "wall / roof / floor envelope data;"),
    ("bullet", "energy consumption by energy carrier;"),
    ("bullet", "improvement measures."),
    ("body", "Some Lisbon mappings therefore require joining multiple files using the "
             "anonymized certificate ID."),

    ("heading", "How to use the file"),
    ("body", "Read the main table row by row:"),
    ("bullet", "Start from the canonical concept and its definition / unit."),
    ("bullet", "Check the comparability risk and verdict to know how safely it can be "
               "compared."),
    ("bullet", "Optionally check the TIMEPAC cross-reference / Italian XPath."),
    ("bullet", "Look at the source column, match type and notes for each city."),
    ("body", "The workbook is a first structured mapping and comparison tool. Partial "
             "or Uncertain mappings, and any concept marked conditional or "
             "not_comparable, may need further validation with official data "
             "dictionaries before the values are treated as equivalent."),
]


def set_single_text(p_el, text):
    """Keep the first run of a paragraph, drop the rest, set its text."""
    runs = p_el.findall(qn("w:r"))
    for r in runs[1:]:
        p_el.remove(r)
    r0 = runs[0]
    for child in list(r0):
        if child.tag in (qn("w:t"), qn("w:br")):
            r0.remove(child)
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = text
    r0.append(t)
    return p_el


def strip_numpr(p_el):
    ppr = p_el.find(qn("w:pPr"))
    if ppr is not None:
        num = ppr.find(qn("w:numPr"))
        if num is not None:
            ppr.remove(num)


def ensure_bold(rpr):
    if rpr.find(qn("w:b")) is None:
        rpr.insert(0, OxmlElement("w:b"))


def main():
    shutil.copyfile(DOC, BACKUP)
    doc = Document(str(DOC))
    paras = doc.paragraphs

    # templates cloned from the original document
    title_t = copy.deepcopy(paras[0]._p)     # bold red underline 14pt
    bullet_t = copy.deepcopy(paras[5]._p)     # NormalWeb + numId 2 (bullet)
    body_t = copy.deepcopy(paras[11]._p)      # plain NormalWeb
    def_t = copy.deepcopy(paras[30]._p)       # label run + br + text run

    def make_body(text):
        return set_single_text(copy.deepcopy(body_t), text)

    def make_bullet(text):
        return set_single_text(copy.deepcopy(bullet_t), text)

    def make_title(text):
        return set_single_text(copy.deepcopy(title_t), text)

    def make_heading(text):
        p = set_single_text(copy.deepcopy(title_t), text)
        for rpr in p.findall(".//" + qn("w:rPr")):
            col = rpr.find(qn("w:color"))
            if col is not None:
                col.set(qn("w:val"), "1F3864")
            for tag in ("w:sz", "w:szCs"):
                e = rpr.find(qn(tag))
                if e is not None:
                    e.set(qn("w:val"), "26")  # 13pt
            u = rpr.find(qn("w:u"))
            if u is not None:
                rpr.remove(u)
        return p

    def make_def(label, body):
        p = copy.deepcopy(def_t)
        runs = p.findall(qn("w:r"))
        r0 = runs[0]
        # label run -> bold
        for child in list(r0):
            if child.tag in (qn("w:t"), qn("w:br")):
                r0.remove(child)
        rpr = r0.find(qn("w:rPr"))
        if rpr is None:
            rpr = OxmlElement("w:rPr")
            r0.insert(0, rpr)
        ensure_bold(rpr)
        t = OxmlElement("w:t")
        t.set(qn("xml:space"), "preserve")
        t.text = label + " "
        r0.append(t)
        # second run -> br + body text
        for r in runs[2:]:
            p.remove(r)
        r1 = runs[1] if len(runs) > 1 else None
        if r1 is None:
            r1 = OxmlElement("w:r")
            p.append(r1)
        for child in list(r1):
            if child.tag == qn("w:t"):
                r1.remove(child)
        if r1.find(qn("w:br")) is None:
            r1.append(OxmlElement("w:br"))
        t2 = OxmlElement("w:t")
        t2.set(qn("xml:space"), "preserve")
        t2.text = body
        r1.append(t2)
        return p

    builders = []
    for item in CONTENT:
        kind = item[0]
        if kind == "title":
            builders.append(make_title(item[1]))
        elif kind == "heading":
            builders.append(make_heading(item[1]))
        elif kind == "body":
            builders.append(make_body(item[1]))
        elif kind == "bullet":
            builders.append(make_bullet(item[1]))
        elif kind == "def":
            builders.append(make_def(item[1], item[2]))

    body = doc.element.body
    sectpr = body.find(qn("w:sectPr"))
    for p in body.findall(qn("w:p")):
        body.remove(p)
    for el in builders:
        sectpr.addprevious(el)

    doc.save(str(DOC))
    print(f"Updated {DOC}")
    print(f"Backup  {BACKUP}")
    print(f"Paragraphs written: {len(builders)}")


if __name__ == "__main__":
    main()
