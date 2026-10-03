"""
Throwaway preprocessing script: extract London-city rows from the UK domestic EPC dataset.

Source: UK_domestic_EPC.csv (~313 MB, 91 cols, ~323k rows, England + Wales).
Location is clean here: `posttown` holds the town/city (casing is inconsistent).

Rule: keep rows where posttown (trimmed, upper-cased) == "LONDON".
Output: all 91 original columns + one new `city` = "London".
"""
import csv
import sys

SRC = "/Users/keremkose/Desktop/Thesis/Energy Documents/UK/UK_domestic_EPC.csv"
OUT = "/Users/keremkose/Desktop/Thesis/Energy Documents/UK/UK_domestic_EPC_London.csv"

csv.field_size_limit(sys.maxsize)


def main():
    with open(SRC, encoding="utf-8", errors="replace", newline="") as fin, \
         open(OUT, "w", encoding="utf-8", newline="") as fout:
        reader = csv.reader(fin)
        writer = csv.writer(fout)

        header = next(reader)
        pt_idx = header.index("posttown")
        writer.writerow(header + ["city"])

        total = 0
        kept = 0
        for row in reader:
            total += 1
            pt = (row[pt_idx] or "").strip().upper()
            if pt == "LONDON":
                writer.writerow(row + ["London"])
                kept += 1

    print(f"DONE. Scanned {total:,} rows, kept {kept:,} London rows.")
    print(f"Output: {OUT}")


if __name__ == "__main__":
    main()
