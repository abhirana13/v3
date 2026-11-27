"""Number and change formatting shared by the diagnosis text and the Slack report."""
from __future__ import annotations


def usd(v: float | None, decimals: int | None = None) -> str:
    if v is None:
        return "n/a"
    a = abs(v)
    if decimals is None:
        decimals = 0 if a >= 20 else (2 if a >= 1 else 3)
    s = f"{a:,.{decimals}f}"
    return ("-$" if v < 0 else "$") + s


def num(v: float | None, sig: int = 3) -> str:
    if v is None:
        return "n/a"
    a = abs(v)
    if a >= 1000:
        return f"{v:,.0f}"
    if a >= 100:
        return f"{v:.0f}"
    if a >= 10:
        return f"{v:.1f}"
    if a >= 1:
        return f"{v:.2f}"
    return f"{v:.3f}"


def pct(v: float | None, signed: bool = True, decimals: int = 0) -> str:
    """0.123 -> '+12%' (signed) or '12%'."""
    if v is None:
        return "n/a"
    s = f"{abs(v) * 100:.{decimals}f}%"
    if not signed:
        return s
    return ("-" if v < 0 else "+") + s


def arrow_pct(v: float | None, decimals: int = 0) -> str:
    """-0.4 -> '▼40%', +0.1 -> '▲10%'."""
    if v is None:
        return "n/a"
    return ("▼" if v < 0 else "▲") + f"{abs(v) * 100:.{decimals}f}%"


def value(v: float | None, unit: str) -> str:
    if v is None:
        return "n/a"
    if unit == "usd":
        return usd(v)
    if unit == "share":
        return pct(v, signed=False, decimals=1 if v < 0.2 else 0)
    if unit == "count":
        return f"{v:,.0f}"
    return num(v)


def pts(v: float | None) -> str:
    """share-point change: 0.06 -> '+6 pts'."""
    if v is None:
        return "n/a"
    return ("+" if v >= 0 else "-") + f"{abs(v) * 100:.0f} pts"
