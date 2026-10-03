"""
Re-filter Amsterdam to residential-only.

Amsterdam_city.csv was filtered by postcode (1011-1109) only, so it includes
non-residential buildings. The Dutch EP-Online `Gebouwklasse` field separates:
  W = Woningbouw     -> residential
  U = Utiliteitsbouw -> non-residential (offices, shops, ...)

Keep only Gebouwklasse == 'W'. The postcode-only file is left intact.
"""
import csv
import sys

SRC = "/Users/keremkose/Desktop/Thesis/Energy Documents/Amsterdam/Amsterdam_city.csv"
OUT = "/Users/keremkose/Desktop/Thesis/Energy Documents/Amsterdam/Amsterdam_residential.csv"
csv.field_size_limit(sys.maxsize)


def main():
    with open(SRC, encoding="utf-8", errors="replace", newline="") as fin, \
         open(OUT, "w", encoding="utf-8", newline="") as fout:
        r = csv.reader(fin)
        w = csv.writer(fout)
        header = next(r)
        gk = header.index("Gebouwklasse")
        w.writerow(header)
        total = kept = 0
        for row in r:
            total += 1
            if len(row) > gk and row[gk].strip().upper() == "W":
                w.writerow(row)
                kept += 1
    print(f"DONE. Scanned {total:,}, kept {kept:,} residential (W) rows.")
    print(f"Dropped non-residential (U/other): {total - kept:,}")
    print(f"Output: {OUT}")


if __name__ == "__main__":
    main()
