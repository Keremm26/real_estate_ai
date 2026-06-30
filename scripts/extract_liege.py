"""
Extract Liège residential EPC rows from the Walloon PEB open dataset (ODWB / Opendatasoft).

Dataset: peb-certification-residentielle-batiment-existant (existing residential, already
residential-only). Filter to commune = Liège via the Opendatasoft export API (streams the
full filtered CSV, no pagination/cap). Adds a `city` column.
"""
import csv
import io
import urllib.parse
import urllib.request

DS = "peb-certification-residentielle-batiment-existant"
EXPORT = f"https://www.odwb.be/api/explore/v2.1/catalog/datasets/{DS}/exports/csv"
OUT = "/Users/keremkose/Desktop/Thesis/Energy Documents/Belgium:Liege/Liege_residential.csv"


def main():
    params = {
        "where": 'communes = "Liège"',
        "delimiter": ",",
        "with_bom": "false",
    }
    url = EXPORT + "?" + urllib.parse.urlencode(params)
    print("Downloading Liège export...", flush=True)
    raw = urllib.request.urlopen(url, timeout=300).read().decode("utf-8")

    reader = csv.reader(io.StringIO(raw))
    rows = list(reader)
    header = rows[0] + ["city"]
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for row in rows[1:]:
            w.writerow(row + ["Liège"])
    print(f"DONE. Wrote {len(rows)-1:,} Liège residential rows ({len(header)} cols).")
    print(f"Output: {OUT}")


if __name__ == "__main__":
    main()
