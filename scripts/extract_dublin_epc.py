"""
Throwaway preprocessing script: extract Dublin-city rows from the Ireland EPC dataset.

The source (Ireland_EPC.xlsx, ~849 MB, 211 cols) has no clean city column.
Location lives in `CountyName`, which mixes three formats:
  - "Co. X"          -> county (rural / whole county)        -> EXCLUDED
  - "X City"         -> Cork/Galway/Limerick/Waterford City  -> EXCLUDED
  - "Dublin <n>[W]"  -> Dublin city postal district          -> KEPT  (this is Dublin city)
  - "Co. Dublin"     -> county Dublin (suburban ring)        -> EXCLUDED (not city proper)

Output: a CSV with all 211 original columns + two new ones:
  - city            = "Dublin"
  - dublin_district = original CountyName value, e.g. "Dublin 7"

The file is too large for pandas/openpyxl-normal mode, so we stream it row by row
with openpyxl read_only and write matching rows straight to CSV.
"""
import csv
import re
import sys
import openpyxl

SRC = "/Users/keremkose/Desktop/Thesis/Energy Documents/IRELAND_EPC/Ireland_EPC.xlsx"
OUT = "/Users/keremkose/Desktop/Thesis/Energy Documents/IRELAND_EPC/Ireland_EPC_Dublin_city.csv"

# Dublin city postal districts: "Dublin 1" .. "Dublin 24", plus "Dublin 6W".
DUBLIN_RE = re.compile(r"^Dublin\s+\d+W?$", re.IGNORECASE)


def main():
    wb = openpyxl.load_workbook(SRC, read_only=True, data_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)

    header = list(next(rows))
    cn_idx = header.index("CountyName")
    out_header = header + ["city", "dublin_district"]

    total = 0
    kept = 0
    district_counts = {}

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(out_header)
        for row in rows:
            total += 1
            cn = row[cn_idx]
            if cn is not None and DUBLIN_RE.match(str(cn).strip()):
                writer.writerow(list(row) + ["Dublin", cn])
                kept += 1
                district_counts[cn] = district_counts.get(cn, 0) + 1
            if total % 200000 == 0:
                print(f"  scanned {total:,} rows, kept {kept:,}...", flush=True)

    wb.close()

    print(f"\nDONE. Scanned {total:,} rows, kept {kept:,} Dublin-city rows.")
    print(f"Output: {OUT}")
    print("\nRows per district:")
    for d, c in sorted(district_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {c:>7,}  {d}")


if __name__ == "__main__":
    main()
