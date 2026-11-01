"""HTML parsing helpers for mobile.ir pages (no HTTP here)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from bs4 import BeautifulSoup, Tag

TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))
KINDS = {"phones": "phone", "tablets": "tablet", "smartwatches": "smartwatch"}
STATUS = {"Available": "available", "ComingSoon": "coming_soon", "Discontinued": "discontinued", "Canceled": "canceled"}
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_PHONE_RE = re.compile(r"(?:\+98|0)\d[\d\s.\-]{8,}\d")


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def txt(el: Tag | None) -> str:
    """Whitespace-collapsed text of an element ('' when missing), with ASCII digits."""
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).translate(_DIGITS) if el else ""


def num(s: str) -> int | None:
    """First integer in a string such as '249,980,000 تومان' (None when there is none)."""
    m = re.search(r"\d[\d,]*", s.translate(_DIGITS))
    return int(m.group().replace(",", "")) if m else None


def flt(s: str) -> float | None:
    m = re.search(r"\d+(?:\.\d+)?", s.translate(_DIGITS))
    return float(m.group()) if m else None


def redact(s: str) -> str:
    """Hide phone numbers that sellers type into free text (personal data)."""
    return _PHONE_RE.sub("[phone]", s)


def clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def kind_of(href: str) -> str | None:
    m = re.search(r"/(phones|tablets|smartwatches)/", href)
    return KINDS[m.group(1)] if m else None


def total(s: str) -> int | None:
    """The 'N مورد' total of a list page (paginationbox text or the finder's h3)."""
    m = re.search(r"از مجموع (\d+)", s.translate(_DIGITS)) or re.search(r"(\d+) مورد", s.translate(_DIGITS))
    return int(m.group(1)) if m else None


def filetime(v: str) -> str | None:
    """Windows FILETIME (100 ns ticks since 1601), as the prices table gives it, to an ISO time in Tehran."""
    try:
        sec = (int(v) - 116444736000000000) / 1e7
    except ValueError:
        return None
    return datetime.fromtimestamp(sec, TEHRAN_TZ).isoformat(timespec="minutes")


def cards(s: BeautifulSoup) -> list[dict[str, Any]]:
    """Product cards of finder and brand lists."""
    out = []
    for p in s.select(".product"):
        a = p.select_one(".product__title")
        m = re.search(r"specifications-(\d+)-", a["href"]) if a else None
        if not m:
            continue
        c: dict[str, Any] = {
            "id": int(m.group(1)),
            "brand": txt(p.select_one(".product__brand")),
            "name": txt(p.select_one(".product__name")),
            "kind": kind_of(a["href"]),
            "status": next((STATUS[k] for k in p.get("class", []) if k in STATUS), None),
        }
        r = p.select_one(".product__rating")
        if r:
            c["rating"] = flt(txt(r))
            c["votes"] = num(txt(r.find("span")))
        for sp in p.select(".product__info > span"):
            t = txt(sp)
            if t.startswith("عرضه"):
                c["year"] = num(t)
            elif t.startswith("صفحه"):
                c["screen_inch"] = flt(t)
            elif t.startswith("دوربین"):
                c["camera_mp"] = num(t)
        price = p.select_one(".product__price")
        if price:
            c["price_toman"] = num(txt(price))
        out.append(c)
    return out


def specs(s: BeautifulSoup) -> dict[str, dict[str, str]]:
    """Spec sections of a phone page: {section title: {label: value}}."""
    out: dict[str, dict[str, str]] = {}
    for div in s.select(".product-spec > div"):
        h3 = div.find("h3")
        if not h3:
            continue
        title = txt(h3.find(string=True, recursive=False)) or txt(h3)
        items: dict[str, str] = {}
        last = ""
        for li in div.select("ul > li"):
            if "details" in li.get("class", []):
                pre = li.find("pre")
                if last and pre:
                    items[last] += " | " + "; ".join(x.strip() for x in pre.get_text().splitlines() if x.strip())
                continue
            label = txt(li.find("span")) or "?"
            strong = li.find("strong")
            if strong is None:
                continue
            img = strong.find("img")
            if img:
                value = "yes" if "Yes" in img.get("src", "") else "no"
            else:
                for br in strong.find_all("br"):
                    br.replace_with("\n")
                value = " | ".join(x.strip() for x in strong.get_text().translate(_DIGITS).splitlines() if x.strip())
            key, n = label, 2
            while key in items:
                key, n = f"{label} {n}", n + 1
            items[key] = value
            last = key
        out[title] = items
    return out
