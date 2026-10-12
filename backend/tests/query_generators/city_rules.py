"""Legal value formulas for the London and Madrid query sets (downstream and
pipeline generators). Each function returns the accepted [value_min, value_max]
of a building-level surface requirement; the bases are written out in the
generated files' meta.value_basis. tests/validate_query_files.py recomputes
the same ranges independently (city_range).
"""
import math

HEURISTIC = 10.0          # building agent rule 5: ~10 m2 per person


def stratum(vmin, persons):
    """Is the legal minimum above the building agent's capacity estimate?"""
    if vmin is None or not persons:
        return None
    return "law_above_heuristic" if vmin > HEURISTIC * persons else "law_at_or_below_heuristic"


def known(v):
    return v, round(1.25 * v, 2)


# ------------------------------------------------------------------ London
def hmo_nat(s=0, d=0):            # SI 2018/616 para 1A(2): 6.51 / 10.22
    return round(6.51 * s + 10.22 * d, 2)


def hmo_nat_unknown(n):            # all doubles (5.11 N) .. all singles + 25 %
    return round(5.11 * n, 2), round(1.25 * 6.51 * n, 2)


def th_kitchen(n):                 # TH shared kitchen by sharers
    if n <= 3:
        return 5.5
    if n <= 5:
        return 7.5
    if n <= 7:
        return 9.5
    if n <= 10:
        return 11.5
    raise ValueError(n)


def th_rooms(s=0, d=0, bedsit=False):   # TH sleeping rooms: 8.5 / 13 (13 / 18 with kitchen inside)
    return round((13 * s + 18 * d) if bedsit else (8.5 * s + 13 * d), 2)


def th_range(s=0, d=0):            # rooms .. (rooms + shared kitchen) + 25 %
    t = th_rooms(s, d)
    return t, round(1.25 * (t + th_kitchen(s + 2 * d)), 2)


def th_unknown(n):
    return round(6.5 * n, 2), round(1.25 * (8.5 * n + th_kitchen(n)), 2)


T31 = {  # London Plan Table 3.1: (bedrooms, persons) -> GIA by storeys (1, 2, 3)
    (1, 1): (39, None, None), (1, 2): (50, 58, None), (2, 3): (61, 70, None), (2, 4): (70, 79, None),
    (3, 4): (74, 84, 90), (3, 5): (86, 93, 99), (3, 6): (95, 102, 108), (4, 5): (90, 97, 103),
    (4, 6): (99, 106, 112), (4, 7): (108, 115, 121),
}


def lp_range(b, p, storeys=None, shower_studio=False):
    vals = [v for v in T31[(b, p)] if v is not None]
    if storeys:
        g = T31[(b, p)][storeys - 1]
        return g, round(1.25 * g, 2)
    if len(vals) == 1:                       # 1b1p: 39 (37 with a shower room)
        return (37 if shower_studio else 39), round(1.25 * 39, 2)
    return min(vals), max(vals)


# ------------------------------------------------------------------ Madrid
def prog(r):
    """PGOUM art. 7.3.4 minimum programme with r bedrooms: living-dining 14 +
    kitchen 7 + bathroom 3 + first bedroom 12 + 7 per additional bedroom, and
    never below the 40 m2 minimum dwelling."""
    return max(40, 29 + 7 * r)


def prog_unknown(n):           # n beds / persons, 1-2 per bedroom: r in [ceil(n/2), n]
    return prog(math.ceil(n / 2)), round(1.25 * prog(n), 2)


def shared(s=0, d=0):
    """Art. 7.3.11.3 residencia compartida: private unit >= max(15, 10 per person)
    (single 15, double 20) + common areas 25 + 5 per additional unit."""
    return 15 * s + 20 * d + 25 + 5 * (s + d - 1)


def shared_unknown(n):         # most doubles .. all singles + 25 %
    return shared(n % 2, n // 2), round(1.25 * shared(n, 0), 2)
