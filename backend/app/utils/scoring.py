import pandas as pd
import numpy as np
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

# Operators the rankers score numerically (after normalize_operator).
CONTINUOUS_OPERATORS = {">=", ">", "<=", "<"}
NUMERIC_OPERATORS = CONTINUOUS_OPERATORS | {"==", "BETWEEN"}


def normalize_operator(operator: Any) -> str:
    """Upper-cased operator with the SQL-style '=' folded into '=='."""
    op = str(operator or "").strip().upper()
    return "==" if op == "=" else op


def to_number(value: Any) -> Optional[float]:
    """float(value) for scalars that are numbers (or numeric strings), else None."""
    if value is None or isinstance(value, (bool, list, tuple, dict, set)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(number) else number


def to_range(value: Any) -> Optional[Tuple[float, float]]:
    """(low, high) for a two-element numeric list/tuple (BETWEEN), else None."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        lo, hi = to_number(value[0]), to_number(value[1])
        if lo is not None and hi is not None:
            return (min(lo, hi), max(lo, hi))
    return None


def is_numeric_column(series: pd.Series, sample_size: int = 500) -> bool:
    """True for numeric dtypes and for text columns holding numbers
    (e.g. epoca_costruzione: years stored as VARCHAR)."""
    if pd.api.types.is_bool_dtype(series):
        return False
    if pd.api.types.is_numeric_dtype(series):
        return True
    sample = series.dropna().head(sample_size)
    if sample.empty:
        return False
    return bool(pd.to_numeric(sample, errors="coerce").notna().mean() >= 0.95)


def requirement_is_scorable(
    requirement: Dict[str, Any],
    df: pd.DataFrame,
    allowed_columns: Optional[Iterable[str]] = None,
    require_value: bool = True,
) -> bool:
    """Would a ranker actually score this requirement against ``df``?

    Shared by the rankers (to skip what they cannot score) and by the
    orchestrator's "found" predicate, so an agent never keeps ranking weight
    for requirements that score a constant 0. Expects ``target_column`` already
    resolved to a dataframe column.
    """
    col = requirement.get("target_column")
    if not isinstance(col, str) or col not in df.columns:
        return False
    if allowed_columns is not None and col not in set(allowed_columns):
        return False
    value = requirement.get("value")
    if value is None:
        return not require_value
    op = normalize_operator(requirement.get("operator"))
    if op in NUMERIC_OPERATORS and is_numeric_column(df[col]):
        # A numeric column only scores numeric targets; text like 'D' on a
        # year column, or a malformed BETWEEN, would score 0 for everyone.
        return to_range(value) is not None if op == "BETWEEN" else to_number(value) is not None
    return True


def calculate_range_score(series: pd.Series, low: float, high: float) -> pd.Series:
    """BETWEEN: 100 inside [low, high], decaying linearly to 0 at one range
    width outside it (a 85 m2 property scores 83 for 'tra 50 e 80 mq')."""
    vals = pd.to_numeric(series, errors="coerce")
    width = max(float(high) - float(low), 1.0)
    distance = (float(low) - vals).clip(lower=0) + (vals - float(high)).clip(lower=0)
    return (100.0 - distance / width * 100.0).clip(0, 100).fillna(0.0).round(1)


def calculate_equality_score(
    series: pd.Series, target: float, col_stats: Optional[Dict[str, Any]] = None
) -> pd.Series:
    """'==' on a numeric column: 100 at the target, decaying with distance
    relative to the column's interquartile range (robust to outliers such as
    the 15.9M m2 surface record that made a min-max range score everyone ~100).
    Falls back to the min-max range, then to exact match."""
    vals = pd.to_numeric(series, errors="coerce")
    spread = 0.0
    if isinstance(col_stats, dict):
        pct = col_stats.get("percentiles") or {}
        p25, p75 = to_number(pct.get("25%")), to_number(pct.get("75%"))
        if p25 is not None and p75 is not None and p75 > p25:
            spread = p75 - p25
        elif to_number(col_stats.get("max")) is not None and to_number(col_stats.get("min")) is not None:
            spread = float(col_stats["max"]) - float(col_stats["min"])
    if spread > 0:
        score = (100.0 - (vals - float(target)).abs() / spread * 100.0).clip(0, 100)
    else:
        score = (vals == float(target)).astype(float) * 100.0
    return score.fillna(0.0).round(1)

def calculate_continuous_score(
    series: pd.Series, 
    target: Union[float, int], 
    operator: str = ">=", 
    exclusive: bool = False
) -> pd.Series:
    """
    Calculates a score between 0 and 100 for continuous variables using 50/100 logic.
    
    Rules for '>=' (Minimum required):
    - Value = Target: 50 points
    - Value = 2 * Target: 100 points
    - Value < Target: 0 points (if exclusive or op == '>')
    
    Rules for '<=' (Maximum required):
    - Value = Target: 50 points
    - Value = Target / 2: 100 points
    - Value > Target: 0 points (if exclusive or op == '<')
    
    Args:
        series: Pandas series of numerical values.
        target: Reference value searched by the user.
        operator: Comparison operator (>=, >, <=, <).
        exclusive: If True, the limit is categorical (under/over threshold = 0).
        
    Returns:
        pd.Series: Calculated scores limited to [0, 100].
    """
    if target is None:
        return pd.Series(0.0, index=series.index)
    
    # Ensure values are numerical
    vals = pd.to_numeric(series, errors="coerce").fillna(0)
    T = float(target)
    
    op = str(operator).upper()
    
    if op in [">=", ">"]:
        if T > 0:
            # Formula: (value / T * 50) -> at T gives 50, at 2T gives 100
            score = (vals / T * 50).clip(0, 100)
            
            # Exclusivity / operator rigor handling
            if op == ">" or exclusive:
                score = score.mask(vals <= T, 0.0)
            else: # >=
                score = score.mask(vals < T, 0.0)
        else:
            # If target is 0, any value >= 0 is a perfect match (100)
            score = pd.Series(100.0, index=series.index)
            if op == ">":
                score = score.mask(vals <= 0, 0.0)
            
    elif op in ["<=", "<"]:
        if T > 0:
            # Formula: 150 - (value / T * 100) -> at T gives 50, at T/2 gives 100
            score = (150 - (vals / T * 100)).clip(0, 100)
            
            # Exclusivity handling
            if op == "<" or exclusive:
                score = score.mask(vals >= T, 0.0)
            else: # <=
                score = score.mask(vals > T, 0.0)
        else:
            # If target is 0, only values <= 0 (so 0) are valid
            score = (vals <= 0).astype(float) * 100.0
    else:
        # Fallback for equality if not handled otherwise
        score = (vals == T).astype(float) * 100.0
        
    return score.round(1)

def calculate_discrete_score(
    series: pd.Series, 
    preferred_values: List[Any]
) -> pd.Series:
    """
    Calculates a score between 0 and 100 for discrete variables (categorical/ordinal).
    
    Rules:
    - Single choice: Perfect match = 100 points, otherwise 0.
    - Multiple choices (ordered):
        - First choice (Best): 100 points
        - Last choice (Minimum acceptable): 50 points
        - Intermediate: linear scale between 100 and 50.
        
    Args:
        series: Pandas series of values (strings, typologies, energy classes).
        preferred_values: List of accepted values, ordered by decreasing preference.
        
    Returns:
        pd.Series: Calculated scores.
    """
    if not preferred_values:
        return pd.Series(0.0, index=series.index)
    
    # Input cleaning
    vals_clean = series.astype(str).str.lower().str.strip()
    target_list = [str(v).lower().strip().strip("'\"") for v in preferred_values]
    
    n_target = len(target_list)
    mapping = {}
    
    if n_target == 1:
        # Single choice = Perfect match 100
        mapping[target_list[0]] = 100.0
    else:
        for idx, t in enumerate(target_list):
            # Formula: First choice (idx=0) = 100, Last (idx=n-1) = 50
            score = round(100.0 - (idx / (n_target - 1) * 50.0), 1)
            mapping[t] = score
            
    def get_score(v):
        v_str = str(v).lower().strip()
        # Case 1: Exact match in dictionary
        if v_str in mapping:
            return mapping[v_str]
        
        # Case 2: Partial match (if value in DB contains or is contained in one of the targets)
        for t, s in mapping.items():
            if t in v_str or v_str in t:
                return s
        return 0.0

    return vals_clean.apply(get_score)
