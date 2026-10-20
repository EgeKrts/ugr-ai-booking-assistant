"""Pure, deterministic parsers for numbers, weights, dimensions and dates.

Each parser returns a value only when the text supports it. Anything unclear
returns a code instead of a guess, so the validator can ask for review.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
import re
import unicodedata

# ---------------------------------------------------------------- text folding

def fold(text: str) -> str:
    """Form that ignores capitalisation, accents and punctuation, used for master-data matching."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    stripped = stripped.casefold().replace("ı", "i")
    return re.sub(r"[^0-9a-z]+", " ", stripped).strip()


def squash(text: str) -> str:
    """Whitespace-insensitive form for checking that a value occurs in the source."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip().casefold()


# ------------------------------------------------------------ localized numbers

_NUMBER = r"\d{1,3}(?:[ .,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"


def parse_localized_number(text: str) -> float | None:
    """Read 7 450, 7.650, 12,400, 12.500,50 or 7,5 into a float.

    Two separators: the last one is the decimal mark. One separator followed by
    exactly three digits (and 1-3 digits before it) is a thousands separator;
    otherwise it is a decimal mark. Returns None if the shape is not recognised.
    """
    s = text.strip().replace(" ", " ")
    if re.fullmatch(r"\d{1,3}(?: \d{3})+(?:[.,]\d+)?", s):
        s = s.replace(" ", "")
    if not re.fullmatch(r"\d+(?:[.,]\d+)*", s):
        return None
    dots, commas = s.count("."), s.count(",")
    if dots and commas:
        decimal = "." if s.rfind(".") > s.rfind(",") else ","
        thousands = "," if decimal == "." else "."
        s = s.replace(thousands, "").replace(decimal, ".")
    elif dots + commas:
        mark = "." if dots else ","
        parts = s.split(mark)
        if len(parts) > 2:
            if not all(len(p) == 3 for p in parts[1:]):
                return None
            s = "".join(parts)
        else:
            before, after = parts
            if len(after) == 3 and 1 <= len(before) <= 3 and before != "0":
                s = before + after
            else:
                s = before + "." + after
    return float(s)


# ----------------------------------------------------------------------- weight

@dataclass(frozen=True)
class WeightResult:
    kg: float | None = None
    per_unit: bool = False      # the text says "each" / "per unit"
    code: str | None = None     # UNIT_UNRESOLVED, UNIT_UNSUPPORTED, WEIGHT_UNPARSEABLE, WEIGHT_AMBIGUOUS
    detail: str = ""


_KG_UNITS = {"kg", "kgs", "kilo", "kilos", "kilogram", "kilograms"}
_EACH = re.compile(r"\b(each|per\s+(unit|vehicle|piece)|c/u|cada\s+uno)\b", re.IGNORECASE)


def parse_weight(text: str) -> WeightResult:
    matches = list(re.finditer(_NUMBER, text))
    if not matches:
        return WeightResult(code="WEIGHT_UNPARSEABLE", detail="No number found")
    if len(matches) > 1:
        return WeightResult(code="WEIGHT_AMBIGUOUS", detail="More than one number found")
    number = matches[0]
    value = parse_localized_number(number.group())
    if value is None or value <= 0:
        return WeightResult(code="WEIGHT_UNPARSEABLE", detail=f"Cannot read {number.group()!r}")
    unit_match = re.match(r"\s*([A-Za-z]+)", text[number.end():])
    unit = unit_match.group(1).casefold() if unit_match else ""
    per_unit = bool(_EACH.search(text))
    if unit in _KG_UNITS:
        return WeightResult(kg=value, per_unit=per_unit)
    if unit and unit not in {"each", "per", "c", "cada"}:
        return WeightResult(per_unit=per_unit, code="UNIT_UNSUPPORTED", detail=f"Unit {unit!r} is not converted")
    return WeightResult(per_unit=per_unit, code="UNIT_UNRESOLVED", detail="No weight unit written")


# ------------------------------------------------------------------- dimensions

@dataclass(frozen=True)
class DimensionResult:
    length_m: float | None = None
    width_m: float | None = None
    height_m: float | None = None
    code: str | None = None     # UNIT_UNRESOLVED, DIMENSION_FORMAT_MIXED, DIMENSION_UNPARSEABLE, DIMENSION_OUT_OF_RANGE
    detail: str = ""


_DIMS = re.compile(
    r"(\d[\d.,]*)\s*[x×*]\s*(\d[\d.,]*)\s*[x×*]\s*(\d[\d.,]*)\s*(mm|cm|m|meters?|metres?)?(?![A-Za-z])",
    re.IGNORECASE,
)
_UNIT_TO_M = {"mm": 0.001, "cm": 0.01, "m": 1.0, "meter": 1.0, "meters": 1.0, "metre": 1.0, "metres": 1.0}


# "L 6.2  W 2.5  H 3.9 m": labelled values in length, width, height order.
_LABELLED_DIMS = re.compile(
    r"\bL\w*\s*[:=]?\s*(\d[\d.,]*)\s*[,;]?\s*\bW\w*\s*[:=]?\s*(\d[\d.,]*)\s*[,;]?\s*\bH\w*\s*[:=]?\s*(\d[\d.,]*)"
    r"\s*(mm|cm|m|meters?|metres?)?(?![A-Za-z])",
    re.IGNORECASE,
)


def number_tokens(text: str) -> list[str]:
    """The numbers as written, used to check that a value really occurs in the e-mail."""
    return re.findall(r"\d[\d.,]*\d|\d", text)


def parse_dimensions(text: str) -> DimensionResult:
    match = _DIMS.search(text) or _LABELLED_DIMS.search(text)
    if match is None:
        return DimensionResult(code="DIMENSION_UNPARSEABLE", detail="Expected L x W x H with a unit")
    tokens = match.groups()[:3]
    styles = {("comma" if "," in t else "dot" if "." in t else "plain") for t in tokens}
    if any("," in t and "." in t for t in tokens) or {"comma", "dot"} <= styles:
        # e.g. 6,20 x 2.450 x 3,10: is 2.450 metres, or 2450? Do not guess.
        return DimensionResult(code="DIMENSION_FORMAT_MIXED", detail="Mixed decimal marks in one measurement")
    unit = (match.group(4) or "").casefold()
    if not unit:
        return DimensionResult(code="UNIT_UNRESOLVED", detail="No dimension unit written")
    factor = _UNIT_TO_M[unit]
    values = []
    for token in tokens:
        number = float(token.replace(",", "."))
        values.append(round(number * factor, 4))
    if not all(0.1 <= v <= 30 for v in values):
        return DimensionResult(code="DIMENSION_OUT_OF_RANGE", detail=f"Converted values {values} look wrong")
    return DimensionResult(*values)


# ------------------------------------------------------------------------ dates

@dataclass(frozen=True)
class DateResult:
    kind: str                   # exact | invalid | period | lower_bound | missing
    value: date | None = None   # exact date, or the lower bound
    detail: str = ""


_MONTHS = {name.casefold(): i for i, name in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], start=1)}
_NUMERIC_DATE = re.compile(r"(?<!\d)(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})(?!\d)")
_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_NAMED_DATE = re.compile(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})(?!\d)")
_LOWER_BOUND = re.compile(r"\b(after|from|not before|earliest|since)\b", re.IGNORECASE)


def _build(year: int, month: int, day: int) -> tuple[date | None, str]:
    try:
        return date(year, month, day), ""
    except ValueError:
        return None, f"{day:02d}/{month:02d}/{year} is not a real calendar date"


def parse_requested_date(text: str | None) -> DateResult:
    """Day-first for numeric dates (the dataset contains 22/11 and 28/10, which only fit day-first)."""
    if text is None or text.strip().casefold() in {"", "null", "none", "n/a", "na", "-", "tbd"}:
        return DateResult("missing")
    found: list[tuple[int, int, int]] = []
    for m in _ISO_DATE.finditer(text):
        found.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in _NUMERIC_DATE.finditer(text):
        year = int(m.group(3))
        year = year + 2000 if year < 100 else year
        found.append((year, int(m.group(2)), int(m.group(1))))
    for m in _NAMED_DATE.finditer(text):
        month = _MONTHS.get(m.group(2).casefold())
        if month:
            found.append((int(m.group(3)), month, int(m.group(1))))
    if not found:
        return DateResult("period", detail="No calendar date, only a period or phrase")
    if len(found) > 1:
        return DateResult("period", detail="More than one date in the text")
    year, month, day = found[0]
    value, problem = _build(year, month, day)
    if value is None:
        return DateResult("invalid", detail=problem)
    if _LOWER_BOUND.search(text):
        return DateResult("lower_bound", value, "Earliest acceptable date, not a booked date")
    return DateResult("exact", value)
