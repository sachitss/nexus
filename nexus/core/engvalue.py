"""Engineering-aware value comparison.

Rules (deliberately conservative):
* Two values are only "equal" if they are textually equal after whitespace
  normalisation, or both parse as numbers with the *same* unit (or both without
  a unit) and lie within the configured tolerance.
* Values with different units are only treated as equivalent when both units
  are in the same row of the explicit conversion table below, and the profile
  allows unit conversion. The result is then reported as "equivalent", never
  silently as "equal".
* Anything else is a difference.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# unit -> (dimension, factor to SI base). Only exact, unambiguous conversions.
UNITS: dict[str, tuple[str, float]] = {
    # length
    "mm": ("length", 1e-3), "cm": ("length", 1e-2), "dm": ("length", 1e-1), "m": ("length", 1.0), "km": ("length", 1e3),
    # area
    "mm²": ("area", 1e-6), "mm2": ("area", 1e-6), "cm²": ("area", 1e-4), "cm2": ("area", 1e-4), "m²": ("area", 1.0), "m2": ("area", 1.0),
    # volume
    "l": ("volume", 1e-3), "L": ("volume", 1e-3), "dm³": ("volume", 1e-3), "dm3": ("volume", 1e-3), "m³": ("volume", 1.0), "m3": ("volume", 1.0),
    "ml": ("volume", 1e-6),
    # mass
    "g": ("mass", 1e-3), "kg": ("mass", 1.0), "t": ("mass", 1e3),
    # power
    "W": ("power", 1.0), "kW": ("power", 1e3), "MW": ("power", 1e6),
    # pressure
    "Pa": ("pressure", 1.0), "hPa": ("pressure", 1e2), "kPa": ("pressure", 1e3), "MPa": ("pressure", 1e6),
    "mbar": ("pressure", 1e2), "bar": ("pressure", 1e5),
    # volume flow
    "l/s": ("flow", 1e-3), "l/h": ("flow", 1e-3 / 3600), "l/min": ("flow", 1e-3 / 60), "m³/h": ("flow", 1 / 3600),
    "m3/h": ("flow", 1 / 3600), "m³/s": ("flow", 1.0), "m3/s": ("flow", 1.0),
    # mass flow
    "kg/h": ("massflow", 1 / 3600), "kg/s": ("massflow", 1.0),
    # time
    "s": ("time", 1.0), "min": ("time", 60.0), "h": ("time", 3600.0),
    # electrical
    "V": ("voltage", 1.0), "kV": ("voltage", 1e3), "A": ("current", 1.0), "mA": ("current", 1e-3),
    # speed
    "m/s": ("speed", 1.0),
    # temperature differences/levels are NOT converted (°C vs K is offset/delta-ambiguous)
}

_NUM = re.compile(r"^\s*([+-]?(?:\d{1,3}(?:[.\s]\d{3})+|\d+)(?:[.,]\d+)?(?:[eE][+-]?\d+)?|[+-]?[.,]\d+)\s*([^\d\s].*?)?\s*$")


@dataclass
class EngRules:
    abs_tol: float = 0.0            # absolute tolerance (same unit)
    rel_tol: float = 0.0            # relative tolerance, e.g. 1e-6
    allow_unit_conversion: bool = False
    ignore_case: bool = False
    normalize_ws: bool = True
    decimal_comma: str = "auto"     # "auto" | "comma" | "point"
    default_unit: dict[str, str] = field(default_factory=dict)  # field name -> unit (from a profile)


def parse_number(s: str, decimal_comma: str = "auto"):
    """Return (value, unit) or None. Handles '1.234,5 kW', '1,5', '1.5e3 Pa'."""
    m = _NUM.match(s or "")
    if not m:
        return None
    num, unit = m.group(1), (m.group(2) or "").strip()
    if unit.startswith(".") and "." not in num and "," not in num:   # Fortran style '123.' / '1.e5' (VDI 3805)
        rest = unit[1:].strip()
        m2 = re.match(r"^([eE][+-]?\d+)?\s*(.*)$", rest)
        num, unit = num + "." + (m2.group(1) or ""), m2.group(2).strip()
    num = num.replace(" ", "")
    if decimal_comma == "comma" or (decimal_comma == "auto" and "," in num and (num.count(",") == 1 and (
            "." not in num or num.rfind(".") < num.rfind(",")))):
        num = num.replace(".", "").replace(",", ".")
    else:
        if num.count(",") and "." in num:
            num = num.replace(",", "")
        elif num.count(",") == 1:
            num = num.replace(",", ".")
    if num.count(".") > 1:
        return None
    try:
        return float(num), unit
    except ValueError:
        return None


EQUAL, EQUIVALENT, FORMAT_ONLY, DIFFERENT = "equal", "equivalent", "format", "different"


def compare_values(a: str | None, b: str | None, rules: EngRules | None = None) -> str:
    """Classify two values: equal / format (insignificant notation change) /
    equivalent (equal after an explicit unit conversion) / different."""
    rules = rules or EngRules()
    if a is None or b is None:
        return EQUAL if a == b else DIFFERENT
    if a == b:
        return EQUAL
    a2, b2 = a, b
    if rules.normalize_ws:
        a2, b2 = " ".join(a.split()), " ".join(b.split())
    if rules.ignore_case:
        a2, b2 = a2.lower(), b2.lower()
    if a2 == b2:
        return FORMAT_ONLY
    pa, pb = parse_number(a2, rules.decimal_comma), parse_number(b2, rules.decimal_comma)
    if not pa or not pb:
        return DIFFERENT
    (va, ua), (vb, ub) = pa, pb
    if ua == ub:
        if _close(va, vb, rules):
            return FORMAT_ONLY if va == vb else EQUAL
        return DIFFERENT
    if rules.allow_unit_conversion and ua in UNITS and ub in UNITS and UNITS[ua][0] == UNITS[ub][0]:
        if _close(va * UNITS[ua][1], vb * UNITS[ub][1], rules, si=True):
            return EQUIVALENT
    return DIFFERENT


def _close(x, y, rules, si=False):
    if x == y:
        return True
    tol = max(rules.rel_tol * max(abs(x), abs(y)), 0.0 if si else rules.abs_tol)
    if si:
        tol = max(tol, 1e-12 * max(abs(x), abs(y)))
    return abs(x - y) <= tol
