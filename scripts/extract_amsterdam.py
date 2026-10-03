"""
Throwaway preprocessing script: extract Amsterdam-city rows from the national
Netherlands EP-Online dump (mislabeled 'Amsterdam_dataset.csv').

Source B: ~1.5 GB, semicolon-delimited, 6,165,586 rows, ALL of NL.
  - 2 metadata preamble lines (PublicatieDatum;..., LaatstVerwerkteMutatievolgnummer;...)
  - header on line 3 (PascalCase), 42 cols
  - no city/municipality column -> location key is `Postcode` (e.g. "1011AB")

Amsterdam rule: 4-digit postcode in 1011-1109 (the Amsterdam municipality range).
Output: original 42 columns + `city` = "Amsterdam", comma-delimited.
"""
import csv
import sys

SRC = "/Users/keremkose/Desktop/Thesis/Energy Documents/Amsterdam/Amsterdam_dataset.csv"
OUT = "/Users/keremkose/Desktop/Thesis/Energy Documents/Amsterdam/Amsterdam_city.csv"

PC_LO, PC_HI = 1011, 1109
csv.field_size_limit(sys.maxsize)


def pc4(value):
    """Return the 4-digit numeric part of a Dutch postcode, or None."""
    s = (value or "").strip().replace(" ", "")
    head = s[:4]
    return int(head) if head.isdigit() else None


def main():
    with open(SRC, encoding="utf-8", errors="replace", newline="") as fin, \
         open(OUT, "w", encoding="utf-8", newline="") as fout:
        reader = csv.reader(fin, delimiter=";")
        writer = csv.writer(fout)  # comma-delimited output

        next(reader)  # skip PublicatieDatum line
        next(reader)  # skip LaatstVerwerkteMutatievolgnummer line
        header = next(reader)
        pc_idx = header.index("Postcode")
        writer.writerow(header + ["city"])

        total = kept = bad = 0
        for row in reader:
            total += 1
            if len(row) <= pc_idx:
                bad += 1
                continue
            n = pc4(row[pc_idx])
            if n is not None and PC_LO <= n <= PC_HI:
                writer.writerow(row + ["Amsterdam"])
                kept += 1
            if total % 1_000_000 == 0:
                print(f"  scanned {total:,} | kept {kept:,}", flush=True)

    print(f"\nDONE. Scanned {total:,} rows, kept {kept:,} Amsterdam (1011-1109) rows.")
    print(f"Malformed/short rows skipped: {bad:,}")
    print(f"Output: {OUT}")


if __name__ == "__main__":
    main()
