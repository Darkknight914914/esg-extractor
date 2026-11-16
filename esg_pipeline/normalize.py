"""Stage 5a - Normalise: parse reported numbers and convert to one standard unit.

Companies report the same KPI in different units (tCO2e vs ktCO2e, m3 vs
megalitres vs kilolitres). Comparing companies only works after conversion.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# factor converts 1 <unit> into the family's base unit
UNIT_TABLES: dict[str, dict[str, float]] = {
    "emissions": {           # base: tCO2e
        "tco2e": 1, "tco2": 1, "t co2e": 1, "tonnes co2e": 1, "metric tons co2e": 1, "metric tonnes co2e": 1,
        "kgco2e": 1e-3, "kg co2e": 1e-3,
        "ktco2e": 1e3, "kt co2e": 1e3, "thousand tonnes co2e": 1e3, "000 tco2e": 1e3,
        "mtco2e": 1e6, "mt co2e": 1e6, "million tonnes co2e": 1e6,
    },
    "water": {               # base: ML (megalitres)
        "ml": 1, "megalitres": 1, "megaliters": 1,
        "m3": 1e-3, "m³": 1e-3, "cubic metres": 1e-3, "cubic meters": 1e-3,
        "kl": 1e-3, "kilolitres": 1e-3, "kiloliters": 1e-3,
        "thousand m3": 1, "thousand m³": 1, "000 m3": 1, "000 m³": 1,
        "million m3": 1e3, "million m³": 1e3, "mio m3": 1e3, "mio m³": 1e3,
        "gl": 1e3, "gigalitres": 1e3,
        "litres": 1e-6, "liters": 1e-6,
        "gallons": 3.785411784e-6, "million gallons": 3.785411784,
    },
    "mass": {                # base: t
        "t": 1, "tonnes": 1, "tons": 1, "metric tons": 1,
        "kg": 1e-3, "kt": 1e3, "thousand tonnes": 1e3,
    },
    "currency": {            # base: EUR; FX rates are illustrative - swap for real rates
        "eur": 1, "€": 1, "teur": 1e3, "eur thousand": 1e3, "€ thousand": 1e3, "eur m": 1e6, "€m": 1e6,
        "usd": 0.92, "$": 0.92, "usd thousand": 920, "usd m": 0.92e6,
        "gbp": 1.17, "£": 1.17, "gbp thousand": 1170,
        "inr": 0.011, "₹": 0.011, "inr crore": 0.011e7, "inr lakh": 0.011e5,
    },
}

NUM_RE = re.compile(r"-?\d{1,3}(?:[,\s]\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?")


def parse_number(raw: Any) -> Optional[float]:
    """'1,234.5' -> 1234.5 ; '12 345' -> 12345 ; '(1,200)' -> -1200 ; 'n/a' -> None."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    negative = s.startswith("(") and s.endswith(")")
    m = NUM_RE.search(s)
    if not m:
        return None
    val = float(re.sub(r"[,\s]", "", m.group(0)))
    return -val if negative else val


def _clean_unit(unit: str) -> str:
    u = unit.lower().strip()
    u = u.replace("co₂", "co2").replace("‑", "-").replace("’", "'")
    u = re.sub(r"[()]", "", u)
    u = re.sub(r"\s+", " ", u)
    return u.replace("co2-e", "co2e").replace("co2 e", "co2e").replace("co2eq", "co2e")


def convert(value: float, unit: Optional[str], family: str) -> tuple[Optional[float], Optional[str]]:
    """Convert to the family's base unit. Returns (value, None) when unit unknown."""
    table = UNIT_TABLES[family]
    if not unit:
        return None, f"no unit reported (expected {family} unit)"
    u = _clean_unit(unit)
    # "MtCO2e" means million tonnes, but some (mostly US) reports write "mtCO2e"
    # for metric tonnes. Keep the conversion but send it to a human.
    if family == "emissions" and u.startswith("mt") and unit.strip()[:2] != "Mt":
        return value * table["mtco2e"], f"warning: ambiguous unit '{unit}' (million vs metric tonnes)"
    if u in table:
        return value * table[u], None
    # tolerate variants like "tonnes of co2e" / "tCO2e (market-based)"
    compact = u.replace(" of ", " ").replace(" ", "")
    for key, factor in sorted(table.items(), key=lambda kv: -len(kv[0])):
        if key.replace(" ", "") in compact:
            return value * factor, None
    return None, f"unknown unit '{unit}'"


def parse_percent(raw: Any) -> Optional[float]:
    v = parse_number(raw)
    if v is None:
        return None
    # some reports write 0.32 for 32 %
    if 0 < v <= 1 and "%" not in str(raw):
        return round(v * 100, 4)
    return v


def parse_bool(raw: Any) -> Optional[bool]:
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in {"yes", "true", "y", "separated", "separate"}:
        return True
    if s in {"no", "false", "n", "combined"}:
        return False
    return None
