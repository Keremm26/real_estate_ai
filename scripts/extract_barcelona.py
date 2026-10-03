"""
Extract a 150k random sample of Barcelona residential EPC rows from the Catalonia
register (Socrata open data, dataset j6ii-t3w2, 71 fields).

Filter: poblacio = Barcelona AND residential use (us_edifici contains 'habitat' or
'vivienda', excluding 'Terciari'/'Terciario'). Keeps all substantive columns + city.

Socrata has no stable random field, so we stream the full residential pool
(~257k rows, paginated) and reservoir-sample CAP rows uniformly (fixed seed).
"""
import csv
import json
import random
import urllib.parse
import urllib.request

BASE = "https://analisi.transparenciacatalunya.cat"
DS = "j6ii-t3w2"
OUT = "/Users/keremkose/Desktop/Thesis/Energy Documents/Barcelona/Barcelona_residential.csv"
PAGE = 50000
CAP = 150000
SEED = 42
WHERE = (
    "upper(poblacio)='BARCELONA' AND "
    "(lower(us_edifici) like '%habitat%' OR lower(us_edifici) like '%vivienda%')"
)


def get_fields():
    cols = json.load(urllib.request.urlopen(f"{BASE}/api/views/{DS}/columns.json", timeout=60))
    return [c["fieldName"] for c in cols if not c["fieldName"].startswith(":@computed")]


def main():
    fields = get_fields()
    print(f"Fields: {len(fields)} (+city) | target random sample: {CAP:,}", flush=True)

    rng = random.Random(SEED)
    reservoir = []
    seen = 0
    offset = 0
    while True:
        q = {"$where": WHERE, "$order": "num_cas", "$limit": PAGE, "$offset": offset}
        url = f"{BASE}/resource/{DS}.json?" + urllib.parse.urlencode(q)
        data = json.load(urllib.request.urlopen(url, timeout=180))
        if not data:
            break
        for r in data:
            seen += 1
            if len(reservoir) < CAP:
                reservoir.append(r)
            else:
                j = rng.randint(0, seen - 1)
                if j < CAP:
                    reservoir[j] = r
        print(f"  streamed {seen:,} | reservoir {len(reservoir):,}", flush=True)
        offset += PAGE
        if len(data) < PAGE:
            break

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields + ["city"], extrasaction="ignore")
        w.writeheader()
        for r in reservoir:
            r["city"] = "Barcelona"
            w.writerow(r)

    print(f"\nDONE. Pool={seen:,} | wrote {len(reservoir):,} Barcelona residential rows "
          f"({len(fields)+1} cols).")
    print(f"Output: {OUT}")


if __name__ == "__main__":
    main()
