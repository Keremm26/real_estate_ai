"""
Query bank — assembles gold phrases into natural Italian queries across
difficulty tiers, ready to run when the LLM endpoint is available.

Each entry is a (tier, combo) where combo maps a category to ONE gold phrase.
The phrase VALUES are exactly what the gold scorer expects, so scoring is
automatic. build_query() turns a combo into a natural-language query.

Dry run (no model calls) — preview every query + its expected constraints and
verify all phrases exist in the gold:

    python tests/query_bank.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

GOLD_PATH = os.path.join(os.path.dirname(__file__), "constraint_ground_truth.json")


# ---------------------------------------------------------------------------
# Natural-language query builder
# ---------------------------------------------------------------------------

def build_query(combo: dict) -> str:
    """Compose a natural Italian query from a category->phrase combo."""
    parts = []

    tip = combo.get("tipologia_immobile")
    if tip == "abitazione":
        parts.append("Cerco un'abitazione")
    elif tip:
        parts.append(f"Cerco un {tip}")
    else:
        parts.append("Cerco un immobile")

    if combo.get("metratura_totale"):
        parts.append(combo["metratura_totale"])  # e.g. "sopra i 1000 mq"

    if combo.get("punto_di_interesse"):
        parts.append(f"vicino a {combo['punto_di_interesse']}")

    if combo.get("servizi_accessori"):
        parts.append(f"con {combo['servizi_accessori']} nelle vicinanze")

    ce = combo.get("classe_energetica")
    if ce:
        parts.append(f"di {ce}" if ce.startswith("classe") else f"di classe energetica {ce}")

    if combo.get("progetto_destinazione_uso"):
        parts.append(f"per un progetto di {combo['progetto_destinazione_uso']}")

    return " ".join(parts) + "."


def phrases_of(combo: dict) -> list:
    """The gold phrases present in a combo (for scoring)."""
    return list(combo.values())


# ---------------------------------------------------------------------------
# The bank — curated combos across difficulty tiers
# ---------------------------------------------------------------------------

QUERY_BANK = [
    # --- Tier 1: simple (single constraint) ---
    {"tier": 1, "combo": {"tipologia_immobile": "ufficio"}},
    {"tier": 1, "combo": {"metratura_totale": "sopra i 1000 mq"}},
    {"tier": 1, "combo": {"classe_energetica": "classe A"}},
    {"tier": 1, "combo": {"servizi_accessori": "mezzi di trasporto"}},
    {"tier": 1, "combo": {"punto_di_interesse": "Porta Nuova"}},
    {"tier": 1, "combo": {"progetto_destinazione_uso": "studentato"}},

    # --- Tier 2: medium (2-3 constraints) ---
    {"tier": 2, "combo": {"tipologia_immobile": "abitazione", "classe_energetica": "C o più efficiente"}},
    {"tier": 2, "combo": {"tipologia_immobile": "ufficio", "metratura_totale": "almeno 200 mq"}},
    {"tier": 2, "combo": {"tipologia_immobile": "negozio", "punto_di_interesse": "Porta Susa"}},
    {"tier": 2, "combo": {"tipologia_immobile": "abitazione", "servizi_accessori": "servizi sanitari", "punto_di_interesse": "Parco del Valentino"}},
    {"tier": 2, "combo": {"metratura_totale": "tra 100 e 150 mq", "classe_energetica": "B o superiore"}},
    {"tier": 2, "combo": {"tipologia_immobile": "albergo", "punto_di_interesse": "Porta Nuova", "servizi_accessori": "mezzi di trasporto"}},
    {"tier": 2, "combo": {"progetto_destinazione_uso": "asilo nido", "punto_di_interesse": "Politecnico di Torino"}},
    {"tier": 2, "combo": {"tipologia_immobile": "abitazione", "servizi_accessori": "parchi e aree verdi", "classe_energetica": "almeno D"}},
    {"tier": 2, "combo": {"tipologia_immobile": "capannone", "metratura_totale": "sopra i 1000 mq"}},

    # --- Tier 3: complex (4-5 constraints) ---
    {"tier": 3, "combo": {"tipologia_immobile": "abitazione", "metratura_totale": "sopra i 1000 mq", "punto_di_interesse": "Porta Susa", "servizi_accessori": "mezzi di trasporto", "classe_energetica": "C o più efficiente"}},
    {"tier": 3, "combo": {"tipologia_immobile": "ufficio", "metratura_totale": "almeno 200 mq", "punto_di_interesse": "Porta Nuova", "servizi_accessori": "mezzi di trasporto"}},
    {"tier": 3, "combo": {"tipologia_immobile": "abitazione", "punto_di_interesse": "Parco del Valentino", "servizi_accessori": "parchi e aree verdi", "classe_energetica": "classe A"}},
    {"tier": 3, "combo": {"progetto_destinazione_uso": "centro per anziani", "punto_di_interesse": "Politecnico di Torino", "servizi_accessori": "servizi sanitari"}},
    {"tier": 3, "combo": {"tipologia_immobile": "negozio", "metratura_totale": "tra 100 e 150 mq", "punto_di_interesse": "Porta Susa", "servizi_accessori": "negozi e supermercati"}},
    {"tier": 3, "combo": {"tipologia_immobile": "abitazione", "metratura_totale": "tra 50 e 80 mq", "classe_energetica": "F o G", "servizi_accessori": "scuole e università", "punto_di_interesse": "Palazzo Nuovo"}},

    # --- Tier 4: tricky (generic typology, upper-bound, future-use + geo) ---
    {"tier": 4, "combo": {"tipologia_immobile": "immobile", "metratura_totale": "non più di 100 mq", "servizi_accessori": "mezzi di trasporto"}},
    {"tier": 4, "combo": {"tipologia_immobile": "ufficio", "metratura_totale": "tra 100 e 150 mq", "classe_energetica": "almeno D", "servizi_accessori": "negozi e supermercati", "punto_di_interesse": "Porta Nuova"}},
    {"tier": 4, "combo": {"progetto_destinazione_uso": "studentato", "metratura_totale": "almeno 200 mq", "punto_di_interesse": "Politecnico di Torino"}},
]


# ---------------------------------------------------------------------------
# Dry run / validation (no model calls)
# ---------------------------------------------------------------------------

def _count_expected(gold, phrases):
    n = 0
    for ph in phrases:
        spec = gold["phrases"].get(ph, {})
        n += len(spec.get("expected", []))
    return n


def main():
    gold = json.load(open(GOLD_PATH, encoding="utf-8"))
    gphrases = set(gold["phrases"].keys())

    missing = []
    by_tier = {}
    print("=" * 90)
    print(f"QUERY BANK PREVIEW — {len(QUERY_BANK)} queries (no model calls)")
    print("=" * 90)
    for entry in QUERY_BANK:
        tier = entry["tier"]
        combo = entry["combo"]
        q = build_query(combo)
        phrases = phrases_of(combo)
        for ph in phrases:
            if ph not in gphrases:
                missing.append(ph)
        exp = _count_expected(gold, phrases)
        by_tier[tier] = by_tier.get(tier, 0) + 1
        print(f"\n[T{tier}] {q}")
        print(f"      phrases: {phrases}")
        print(f"      expected constraints: {exp}")

    print("\n" + "=" * 90)
    print("SUMMARY")
    print("=" * 90)
    for t in sorted(by_tier):
        print(f"  tier {t}: {by_tier[t]} queries")
    print(f"  total: {len(QUERY_BANK)} queries")
    if missing:
        print(f"\n  ⚠ PHRASES NOT IN GOLD: {sorted(set(missing))}")
    else:
        print("\n  ✓ all phrases exist in the gold")


if __name__ == "__main__":
    main()
