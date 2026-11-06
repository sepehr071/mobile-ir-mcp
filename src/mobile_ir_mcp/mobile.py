"""Mobile.ir tools: phone search and finder, specs, shop prices, price history, used phones, SIM numbers."""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from .http import ApiError, fetch
from .parse import (
    TEHRAN_TZ,
    cards,
    clip,
    filetime,
    kind_of,
    num,
    redact,
    soup,
    specs,
    total,
    txt,
)
from .registry import tool

PhoneId = Annotated[int, Field(description="Product id from mi_search/mi_find, e.g. 41113.", ge=1)]
Kind = Literal["phone", "tablet", "smartwatch"]
PATH = {"phone": "phones", "tablet": "tablets", "smartwatch": "smartwatches"}
Province = Annotated[
    int | None,
    Field(description="Province id, mi_options('provinces') (1 = Tehran)."),
]
Days = Literal[1, 2, 7, 14, 30, 90, -1]

SECTIONS = {
    "general": ["مشخصات عمومی"],
    "body": ["بدنه"],
    "display": ["صفحه نمایش"],
    "sound": ["زنگ"],
    "memory": ["حافظه"],
    "camera": ["دوربین پشت", "دوربین جلو"],
    "connectivity": ["امکانات ارتباطی"],
    "features": ["دیگر امکانات"],
    "battery": ["باطری"],
    "misc": ["سایر موارد"],
}
# SIM prefix -> the site's `prenums` id (read from the checkboxes on /simcard/).
SIM_PREFIX = {
    "0910": 21, "0911": 19, "0912": 1, "0913": 13, "0914": 14, "0915": 15, "0916": 16, "0917": 17, "0918": 18,
    "0919": 2, "0990": 32, "0991": 34, "0992": 37, "0993": 39, "0996": 42, "0932": 12, "0900": 40, "0901": 30,
    "0902": 29, "0903": 31, "0904": 38, "0905": 35, "0930": 24, "0933": 25, "0935": 10, "0936": 11, "0937": 20,
    "0938": 22, "0939": 23, "0920": 26, "0921": 28, "0922": 33, "0923": 41, "0998": 36,
}  # fmt: skip
OPERATORS = {
    "mci": ["0910", "0911", "0912", "0913", "0914", "0915", "0916", "0917", "0918", "0919", "0990", "0991", "0992", "0993", "0996"],
    "irancell": ["0900", "0901", "0902", "0903", "0904", "0905", "0930", "0933", "0935", "0936", "0937", "0938", "0939"],
    "rightel": ["0920", "0921", "0922", "0923"],
    "taliya": ["0932"],
    "shatel": ["0998"],
}  # fmt: skip


def _not_found(what: str) -> ApiError:
    return ApiError(f"mobile.ir has no {what} with that id. Look it up with mi_search.", 404)


async def _brands() -> list[dict[str, Any]]:
    s = soup(await fetch("/brands/index.aspx"))
    out = []
    for a in s.select(".brands li a"):
        m = re.search(r"/brands/(\d+)-([^/]+)\.aspx", a.get("href", ""))
        if m:
            en, _, fa = a.get("title", "").partition(" / ")
            out.append({"id": int(m.group(1)), "slug": m.group(2), "name": en.strip(), "name_fa": fa.strip() or txt(a)})
    return out


async def _brand_id(brand: str | int) -> int:
    """Resolve a brand id, English name or Persian name (case-insensitive) to the site's brand id."""
    brands = await _brands()
    b = str(brand).strip().lower()
    for x in brands:
        if b in (str(x["id"]), x["name"].lower(), x["name_fa"], x["slug"]):
            return x["id"]
    hits = [x for x in brands if b and (b in x["name"].lower() or b in x["name_fa"])]
    if len(hits) == 1:
        return hits[0]["id"]
    raise ApiError(f"Unknown brand '{brand}'. Call mi_options('brands') for the list.")


def _phone_url(kind: str, page: str, pid: int) -> str:
    return f"/{PATH[kind]}/{page}-{pid}-x.aspx"  # the site 301s to the canonical slug


@tool("Search phones by name")
async def mi_search(
    query: Annotated[
        str,
        Field(
            description="Part of a model name, English or Persian, e.g. 'galaxy s26'.",
            min_length=1,
            max_length=60,
        ),
    ],
) -> dict[str, Any]:
    """Find phones, tablets and smartwatches on Mobile.ir by name and get their ids (autocomplete, up to about 20).

    Use first when the user names a model. For filters use mi_find.
    """
    rows = await fetch("/phones/ajaxphonesearch.aspx", {"q": query}, kind="json")
    return {
        "count": len(rows),
        "results": [
            {"id": r["id"], "name": r["title"], "kind": kind_of(r["url"])} for r in rows if isinstance(r.get("id"), int)
        ],
    }


@tool("Find phones with filters")
async def mi_find(
    kind: Annotated[Kind, Field(description="Product class.")] = "phone",
    brand: Annotated[str | None, Field(description="Brand name or id; see mi_options('brands').")] = None,
    name_contains: Annotated[str | None, Field(description="Part of the model name.", max_length=40)] = None,
    min_price: Annotated[
        int | None,
        Field(description="Lowest shop price, Toman. Unpriced phones are dropped.", ge=0),
    ] = None,
    max_price: Annotated[int | None, Field(description="Highest shop price, Toman.", ge=0)] = None,
    os: Annotated[list[Literal["android", "ios"]] | None, Field(description="Default both.")] = None,
    min_camera_mp: Annotated[int | None, Field(description="Main camera at least this many MP.", ge=0, le=300)] = None,
    max_weight_g: Annotated[int | None, Field(description="Max weight, grams.", ge=2, le=952)] = None,
    min_rating: Annotated[float | None, Field(description="Min user rating (0-10).", ge=0, le=10)] = None,
    min_year: Annotated[int | None, Field(description="Released this year or later.", ge=1990)] = None,
    market: Annotated[
        Literal["available", "any"],
        Field(description="'any' adds discontinued and cancelled."),
    ] = "available",
    sort: Annotated[
        Literal["rating", "date", "price", "camera", "weight", "name"], Field(description="Order of the list.")
    ] = "rating",
    descending: Annotated[bool, Field(description="Highest first.")] = True,
    limit: Annotated[int, Field(description="Rows to return.", ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """Filter Mobile.ir's phone database like its 'mobile finder': brand, shop price range, OS, camera, weight, name.

    Returns cards with id, rating, year, screen, camera and lowest shop price (Toman). If `found` exceeds `limit`,
    tighten the filters; the site never shows more than 100.
    """
    q: list[tuple[str, Any]] = [("ajaxview", "true"), ("product_classes", kind)]
    q += [("minsize", 0), ("maxsize", 1000), ("mindisplayx", 0), ("maxdisplayx", 0), ("mindisplayy", 0)]
    q += [("maxdisplayy", 0), ("mincpu", 0), ("maxcpu", 3000), ("terms", name_contains or "")]
    q += [
        ("minweight", 2),
        ("maxweight", max_weight_g or 952),
        ("mincamerares", min_camera_mp or 0),
        ("maxcamerares", 300 if min_camera_mp else 41),  # 41 is the slider's top stop
    ]
    if min_camera_mp:
        q.append(("withcamera", "true"))  # the camera range is ignored without it
    q += [("os", o) for o in (os or ["android", "ios"])]
    q += [("status", s) for s in (("0", "1") if market == "available" else ("0", "1", "2", "3"))]
    if brand:
        q.append(("brands", await _brand_id(brand)))
    else:
        q += [("brands", b["id"]) for b in await _brands()]
    if min_price is not None or max_price is not None:
        # The site's slider counts thousands of Toman; 200000 is its top stop.
        q += [
            ("withprice", "true"),
            ("minprice", max(65, (min_price or 0) // 1000)),
            ("maxprice", min((max_price or 200_000_000) // 1000, 200000) if max_price else 200000),
        ]
    site_sort = {
        "rating": "rating",
        "date": "date",
        "price": "price",
        "camera": "camerares",
        "weight": "weight",
        "name": "model",
    }
    q += [("sort", site_sort[sort]), ("dir", "desc" if descending else "asc")]
    s = soup(await fetch(f"/{PATH[kind]}/finder.aspx", q, kind="ajax"))
    rows = cards(s)
    found = total(txt(s.h3)) or len(rows)
    # Rating and year are not site filters: applied to the (at most 100) rows the site returns.
    rows = [r for r in rows if (r.get("rating") or 0) >= (min_rating or 0) and (r.get("year") or 0) >= (min_year or 0)]
    return {"found": found, "shown": min(limit, len(rows)), "phones": rows[:limit]}


@tool("Phone specifications")
async def mi_phone(
    id: PhoneId,
    sections: Annotated[
        list[
            Literal[
                "general", "body", "display", "sound", "memory", "camera", "connectivity", "features", "battery", "misc"
            ]
        ]
        | None,
        Field(description="Default all; pick a few to keep it short."),
    ] = None,
) -> dict[str, Any]:
    """Get the full specification sheet of one phone, tablet or smartwatch from Mobile.ir, with rating and price range.

    Next: mi_shop_prices for who sells it, mi_price_history for the trend.
    """
    s = soup(await fetch(_phone_url("phone", "specifications", id)))
    sheet = specs(s)
    crumbs = [txt(a) for a in s.select(".breadcrumb a")]
    if not sheet or len(crumbs) < 3:
        raise _not_found("product")
    out: dict[str, Any] = {"id": id, "name": crumbs[-1], "kind": None}
    out["brand"] = crumbs[-2] if len(crumbs) >= 3 else None
    canon = next((a["href"] for a in s.select(".breadcrumb a") if "specifications" in a.get("href", "")), "")
    out["kind"] = kind_of(canon)
    out["url"] = "https://www.mobile.ir" + canon if canon else None
    price = s.select_one(".product-page .info .price a")
    if price:
        nums = [num(txt(x)) for x in price.find_all("strong")]
        if len(nums) == 2:
            out["price_toman"] = {"lowest": nums[0], "highest": nums[1]}
    rating = s.select_one(".product-page .rating")
    if rating:
        m = re.search(r"(\d+(?:\.\d+)?)\s*از\s*10\s*\(\s*(\d+)\s*رای", txt(rating))
        if m:
            out["rating"], out["votes"] = float(m.group(1)), int(m.group(2))
    out["highlights"] = {
        txt(d.select_one(".title")): txt(d).replace(txt(d.select_one(".title")), "", 1).strip()
        for d in s.select(".product-page__spec-overview > div")
    }
    if sections:
        wanted = {t for k in sections for t in SECTIONS[k]}
        sheet = {k: v for k, v in sheet.items() if k in wanted}
    out["specs"] = {k: {a: clip(b, 600) for a, b in v.items()} for k, v in sheet.items()}
    return out


@tool("Shop prices of a phone")
async def mi_shop_prices(
    id: PhoneId,
    include_old: Annotated[bool, Field(description="Include expired prices.")] = False,
) -> dict[str, Any]:
    """List what each shop currently charges for a phone on Mobile.ir (price in Toman, shop, city, note, warranty).

    Cheapest first. Prices differ by variant: read each shop's note.
    """
    s = soup(await fetch(_phone_url("phone", "shops", id), {"archive": "true"} if include_old else None))
    box = s.select_one(".product-prices")
    if box is None:
        raise _not_found("product")
    crumbs = [txt(a) for a in s.select(".breadcrumb a")]
    shop_ids = {txt(o): int(o["value"]) for o in s.select("#pricechart_shopid option") if o.get("value", "0") != "0"}
    offers: list[dict[str, Any]] = []
    shop: dict[str, Any] = {}
    for el in box.find_all("div", recursive=False):
        cls = el.get("class", [])
        if "shop" in cls:
            a = el.select_one("strong a")
            href = a.get("href", "") if a else ""
            sid = re.search(r"view\.aspx\?id=(\d+)", href)
            shop = {
                "shop": txt(a),
                "shop_id": int(sid.group(1)) if sid else shop_ids.get(txt(a)),
                "city": txt(el.select_one(".shopprovince")).strip("()"),
            }
        elif "price" in cls:
            full = el.select_one(".comments .full")
            note = txt(full) if full else txt(el.select_one(".comments"))
            note = re.sub(r"\(\s*کمتر\s*\)$", "", note).strip()
            offers.append(
                {
                    **shop,
                    "price_toman": num(txt(el.find("strong"))),
                    "note": clip(redact(html.unescape(note)), 160),
                    "warranty": txt(el.select_one(".warranty")).replace("گارانتی :", "").strip(),
                    "updated": txt(el.select_one(".date")).replace("به روز رسانی :", "").strip(),
                }
            )
    offers.sort(key=lambda o: o["price_toman"] or 10**15)
    return {"id": id, "name": crumbs[-1] if crumbs else None, "offer_count": len(offers), "offers": offers}


@tool("Price history of a phone")
async def mi_price_history(
    id: PhoneId,
    period: Annotated[
        Literal["week", "month", "3months", "6months", "all"], Field(description="How far back.")
    ] = "month",
    shop_id: Annotated[int | None, Field(description="Only this shop (id from mi_shop_prices).")] = None,
) -> dict[str, Any]:
    """Get a phone's shop-price history from Mobile.ir: per-shop first/last/min/max and the daily lowest price in Toman.

    Shops quote different variants: use the daily lowest as the market trend.
    """
    idx = {"week": 0, "month": 1, "3months": 2, "6months": 3, "all": 4}[period]
    data = await fetch(
        "/phones/pricechartjson.aspx",
        {"id": id, "rangeIndex": idx, "shopId": shop_id or 0},
        kind="json",
    )
    shops: dict[str, Any] = {}
    daily: dict[str, int] = {}
    for name, pts in data.items():
        rows = sorted(((int(p[0]), int(p[1])) for p in pts), key=lambda r: r[0])
        if not rows:
            continue
        iso = lambda ms: datetime.fromtimestamp(ms / 1000, TEHRAN_TZ).date().isoformat()  # noqa: E731
        shops[name] = {
            "points": len(rows),
            "first": {"date": iso(rows[0][0]), "price": rows[0][1]},
            "last": {"date": iso(rows[-1][0]), "price": rows[-1][1]},
            "min": min(p for _, p in rows),
            "max": max(p for _, p in rows),
        }
        for ms, price in rows:
            d = iso(ms)
            daily[d] = min(daily.get(d, price), price)
    series = [[d, daily[d]] for d in sorted(daily)]
    if len(series) > 60:  # keep the answer small: thin to about 60 evenly spaced days
        step = len(series) / 60
        series = [series[int(i * step)] for i in range(60)] + [series[-1]]
    return {"id": id, "period": period, "shops": shops, "daily_lowest_toman": series}


@tool("Latest shop prices market-wide")
async def mi_market_prices(
    kind: Annotated[Kind, Field(description="Product class.")] = "phone",
    name_contains: Annotated[str | None, Field(description="Part of the model name.", max_length=40)] = None,
    brand: Annotated[str | None, Field(description="Brand name or id; see mi_options('brands').")] = None,
    province: Province = None,
    shop_id: Annotated[int | None, Field(description="Shop id, mi_options('shops').")] = None,
    min_price: Annotated[int | None, Field(description="Toman, lowest price.", ge=0)] = None,
    max_price: Annotated[int | None, Field(description="Toman, highest price.", ge=0)] = None,
    days: Annotated[Literal[1, 7, 14, 30, 90, -1], Field(description="Updated within this many days (-1 = all).")] = 14,
    sort: Annotated[
        Literal["name", "price", "shop", "province", "date"], Field(description="Order of the rows.")
    ] = "date",
    descending: Annotated[bool, Field(description="Newest / most expensive first.")] = True,
    page_size: Annotated[Literal[50, 100, 150, 200], Field(description="Rows per page.")] = 50,
    page: Annotated[int, Field(description="Page from 1.", ge=1)] = 1,
) -> dict[str, Any]:
    """List the newest price rows that shops posted on Mobile.ir (one row per shop and model), with filters.

    For one model's full offer list use mi_shop_prices.
    """
    q: list[tuple[str, Any]] = [
        ("terms", name_contains or ""),
        ("brandid", await _brand_id(brand) if brand else 0),
        ("provinceid", province or 0),
        ("shopid", shop_id or 0),
        ("duration", days),
        ("pagesize", page_size),
        ("sort", sort),
        ("dir", "desc" if descending else "asc"),
        ("page", page),
    ]
    # The site's price filter is in thousands of Toman and picks from a fixed list; filter exactly here.
    s = soup(await fetch(f"/{PATH[kind]}/prices.aspx", q))
    table = next((t for t in s.find_all("table") if t.find("tbody") and len(t.find_all("tr")) > 3), None)
    rows: list[dict[str, Any]] = []
    for tr in table.find("tbody").find_all("tr", recursive=False) if table else []:
        a = tr.select_one("a.phone")
        tds = tr.find_all("td", recursive=False)
        if a is None or len(tds) < 9:
            continue
        pid = re.search(r"shops-(\d+)-", a["href"])
        price = num(txt(tds[2]))
        if (min_price and (price or 0) < min_price) or (max_price and (price or 0) > max_price):
            continue
        rows.append(
            {
                "id": int(pid.group(1)) if pid else None,
                "name": txt(a),
                "price_toman": price,
                "warranty": clip(txt(tds[5]), 80),
                "shop": txt(tds[6]),
                "city": txt(tds[7]),
                "updated": filetime(tds[8].get("value", "")),
            }
        )
    count = total(txt(s.select_one(".paginationbox .info")))
    return {"total_rows": count, "page": page, "rows": rows}


def _used_card(d) -> dict[str, Any]:
    a = d.select_one("h5 b a")
    m = re.search(r"id=(\d+)", a["href"]) if a else None
    tip = d.select_one("[data-tipurl]")
    spec_id = re.search(r"id=(\d+)", tip["data-tipurl"]) if tip else None
    price_el = d.select_one(".info > span")
    price_txt = txt(price_el)
    more = d.find_next_sibling(class_="moreinfo")
    desc = txt(more.select_one(".desc")).replace("توضیحات فروشنده :", "", 1).strip() if more else ""
    return {
        "ad_id": int(m.group(1)) if m else None,
        "phone_id": int(spec_id.group(1)) if spec_id else None,
        "brand": txt(d.select_one("h5 > span")),
        "model": txt(a),
        "condition": txt(d.select_one(".info strong"))
        or (txt(more.select_one("strong")).split("،")[0].strip() if more else None),
        "price_toman": num(price_txt) if "تومان" in price_txt else None,
        "price_note": None if "تومان" in price_txt else price_txt,
        "city": txt(more.select_one("strong span")) if more else None,
        "updated": txt(more.select_one(".update")).strip("() ").replace("به روز شده در", "").strip() if more else None,
        "photos": len(d.select(".photos img")),
        "description": clip(redact(desc), 240),
    }


@tool("Search used-phone ads")
async def mi_used_search(
    kind: Annotated[Kind, Field(description="Product class.")] = "phone",
    brand: Annotated[str | None, Field(description="Brand name or id; see mi_options('brands').")] = None,
    name_contains: Annotated[str | None, Field(description="Part of the model name.", max_length=40)] = None,
    condition: Annotated[Literal["any", "new", "used"], Field(description="Sealed new or used.")] = "any",
    province: Province = None,
    max_price: Annotated[int | None, Field(description="Toman; unpriced ads dropped.", ge=0)] = None,
    min_price: Annotated[int | None, Field(description="Toman, lowest price.", ge=0)] = None,
    with_photos: Annotated[bool, Field(description="Only ads with photos.")] = False,
    days: Annotated[Literal[2, 7, 30, 90, -1], Field(description="Updated within this many days (-1 = all).")] = 30,
    sort: Annotated[
        Literal["date", "price"],
        Field(description="Unpriced ads sort cheapest; set min_price to skip them."),
    ] = "date",
    descending: Annotated[bool, Field(description="Newest / most expensive first.")] = True,
    page: Annotated[int, Field(description="Page from 1 (40 per page).", ge=1)] = 1,
) -> dict[str, Any]:
    """Search the used and new phone ads that users post on Mobile.ir (price, condition, city, description).

    `price_note` holds non-numeric prices ('توافقی' = negotiable). Open one ad with mi_used_ad.
    """
    q: list[tuple[str, Any]] = [
        ("ajaxview", "true"),
        ("page", page),
        ("product_classes", kind),
        ("title", name_contains or ""),
    ]
    q += [("sbrands", await _brand_id(brand))] if brand else [("all_brands", "on")]
    q += [("status", {"any": 0, "new": 1, "used": -1}[condition]), ("viewtype", "grid"), ("sort", sort)]
    q += [("dir", "desc" if descending else "asc"), ("province", province or -1), ("daterange", days)]
    if max_price is not None or min_price is not None:
        q += [
            ("pricetype", 1 if max_price is not None else 2),
            ("price", max_price if max_price is not None else min_price),
        ]
    else:
        q.append(("priceless", 1))
    if with_photos:
        q.append(("withphotos", "true"))
    s = soup(await fetch("/usedphones/index.aspx", q, kind="ajax"))
    ads = [_used_card(d) for d in s.select(".results .textover .phone")]
    if min_price is not None and max_price is not None:
        ads = [a for a in ads if a["price_toman"] is not None and a["price_toman"] >= min_price]
    return {"total": total(txt(s)) or 0, "page": page, "ads": ads}


@tool("One used-phone ad")
async def mi_used_ad(
    ad_id: Annotated[int, Field(description="ad_id from mi_used_search.", ge=1)],
) -> dict[str, Any]:
    """Read one used/new phone ad on Mobile.ir: price, market price, condition, box, city, description, photos, shops.

    `market_price_toman` is the site's own estimate of today's shop price.
    """
    s = soup(await fetch("/usedphones/info.aspx", {"id": ad_id}))
    box = s.select_one(".usedphoneinfo .content")
    if box is None or box.find("dl") is None:
        raise _not_found("ad")
    fields: dict[str, str] = {}
    for dt in box.find("dl").find_all("dt", recursive=False):
        dd = dt.find_next_sibling("dd")
        if dd is not None:
            fields[txt(dt)] = txt(dd)
    spec = s.select_one(".usedphoneinfo a[href*='specifications-']")
    pid = re.search(r"specifications-(\d+)-", spec["href"]) if spec else None
    price = fields.get("قیمت", "")
    others = []
    for tr in s.select("table.userphones")[0].find_all("tr")[1:6] if s.select("table.userphones") else []:
        tds = [txt(td) for td in tr.find_all("td")]
        link = tr.find("a", href=re.compile(r"info\.aspx\?id="))
        if len(tds) >= 4 and link:
            others.append(
                {
                    "ad_id": int(re.search(r"id=(\d+)", link["href"]).group(1)),
                    "price": tds[0],
                    "condition": tds[1],
                    "updated": tds[2],
                    "city": tds[3],
                }
            )
    shops = []
    tables = s.select("table.userphones")
    if len(tables) > 1:
        for tr in tables[1].find_all("tr")[1:8]:
            tds = [txt(td) for td in tr.find_all("td")]
            if len(tds) >= 6:
                shops.append(
                    {"shop": tds[0], "price_toman": num(tds[1]), "condition": tds[2], "updated": tds[4], "city": tds[5]}
                )
    return {
        "ad_id": ad_id,
        "url": f"https://www.mobile.ir/usedphones/info.aspx?id={ad_id}",
        "title": txt(box.find("h3")),
        "phone_id": int(pid.group(1)) if pid else None,
        "price_toman": num(price) if "تومان" in price else None,
        "price_note": None if "تومان" in price else price,
        "market_price_toman": num(fields.get("حدود قیمت بازار", "")),
        "condition": fields.get("وضعیت"),
        "box": fields.get("جعبه"),
        "location": fields.get("استان / شهر"),
        "updated": fields.get("آخرین به روز رسانی"),
        "description": clip(redact(fields.get("توضیحات فروشنده", "")), 800),
        "photos": [
            "https:" + a["href"] if a["href"].startswith("//") else a["href"]
            for a in s.select(".usedphoneinfo .photos a")
        ][:8],
        "other_ads_same_model": others,
        "shops_selling_model": shops,
    }


@tool("Search SIM card numbers")
async def mi_sim_search(
    number: Annotated[
        str | None,
        Field(
            description="Digits of the 7-digit part after the 09xx prefix, e.g. '1111'.",
            pattern=r"^\d{1,7}$",
        ),
    ] = None,
    match: Annotated[
        Literal["contains", "startswith", "endswith", "equals"], Field(description="How `number` matches.")
    ] = "contains",
    operator: Annotated[
        Literal["mci", "irancell", "rightel", "taliya", "shatel"] | None,
        Field(description="Prefixes per operator: mi_options('operators')."),
    ] = None,
    prefix: Annotated[
        str | None, Field(description="Exact prefix, e.g. 0912 (overrides operator).", pattern=r"^09\d{2}$")
    ] = None,
    condition: Annotated[Literal["any", "new", "used"], Field(description="New (صفر) or used line.")] = "any",
    plan: Annotated[
        Literal["any", "postpaid", "prepaid"],
        Field(description="postpaid = دائمی, prepaid = اعتباری."),
    ] = "any",
    round_only: Annotated[bool, Field(description="Only 'round' (رند) numbers.")] = False,
    province: Province = None,
    max_price: Annotated[int | None, Field(description="Toman, highest price (unpriced ads dropped).", ge=0)] = None,
    min_price: Annotated[int | None, Field(description="Toman, lowest price.", ge=0)] = None,
    days: Annotated[Literal[2, 7, 30, 90, -1], Field(description="Updated within this many days (-1 = all).")] = 30,
    sort: Annotated[
        Literal["date", "price", "number"],
        Field(description="Unpriced ads sort cheapest; set min_price to skip them."),
    ] = "date",
    descending: Annotated[bool, Field(description="Newest / priciest / highest number first.")] = True,
    page: Annotated[int, Field(description="Page from 1 (20 per page).", ge=1)] = 1,
) -> dict[str, Any]:
    """Search the SIM card numbers users sell on Mobile.ir (Iran's numbered-SIM market): operator, number, price.

    Output `number` is the full 11-digit number; open `url` on the site to contact the seller.
    """
    prefixes = [prefix] if prefix else (OPERATORS[operator] if operator else list(SIM_PREFIX))
    q: list[tuple[str, Any]] = [("ajaxview", "true"), ("sp", ""), ("prenum", ""), ("page", page), ("viewtype", "grid")]
    q += [("prenumcode", d) for d in range(10)]
    q += [("prenums", SIM_PREFIX[p]) for p in prefixes]
    q += [
        ("isused", {"any": -1, "new": 0, "used": 1}[condition]),
        ("credit", {"any": -1, "postpaid": 0, "prepaid": 1}[plan]),
    ]
    q += [
        ("sort", sort),
        ("dir", "desc" if descending else "asc"),
        ("numbercondition", "equalsto" if match == "equals" else match),
    ]
    q += [("number", number or ""), ("daterange", days), ("province", province or -1)]
    if max_price is not None or min_price is not None:
        q += [
            ("pricetype", 1 if max_price is not None else 2),
            ("price", max_price if max_price is not None else min_price),
        ]
    else:
        q.append(("priceless", 1))
    if round_only:
        q.append(("round", 1))
    s = soup(await fetch("/simcard/index.aspx", q, kind="ajax"))
    out = []
    for t in s.select(".results .textover"):
        a = t.select_one("a.number")
        if a is None:
            continue
        icons = [txt(x) for x in t.select(".littleicons span")]
        price = txt(t.select_one(".info > span"))
        prefix_txt, tail = txt(a.select_one("h5 span")), txt(a.select_one("h5 b"))
        out.append(
            {
                "sim_id": int(re.search(r"id=(\d+)", a["href"]).group(1)),
                "number": prefix_txt + tail,
                "operator": a.select_one("h5 img")["alt"] if a.select_one("h5 img") else None,
                "price_toman": num(price) if "تومان" in price else None,
                "condition": icons[0] if icons else None,
                "plan": icons[1] if len(icons) > 1 else None,
                "round": bool(icons[2]) if len(icons) > 2 else False,
                "city": txt(t.select_one(".moreinfo strong span")),
                "updated": txt(t.select_one(".moreinfo .padd > span"))
                .strip("() ")
                .replace("به روز شده در", "")
                .strip(),
                "url": "https://www.mobile.ir" + a["href"],
            }
        )
    if min_price is not None and max_price is not None:
        out = [x for x in out if x["price_toman"] is not None and x["price_toman"] >= min_price]
    return {"total": total(txt(s.find(string=re.compile("نمایش موارد")))) or 0, "page": page, "numbers": out}


@tool("Brands, provinces, shops, operators")
async def mi_options(what: Literal["brands", "provinces", "shops", "operators"]) -> dict[str, Any]:
    """List the ids the other Mobile.ir tools take: brands, provinces, price-listing shops, SIM operator prefixes.

    Brand names are also accepted directly by `brand` arguments.
    """
    if what == "brands":
        return {"brands": await _brands()}
    if what == "operators":
        return {"operators": OPERATORS, "note": "Pass operator=... or one prefix=... to mi_sim_search."}
    s = soup(await fetch("/phones/prices.aspx"))
    name = "provinceid" if what == "provinces" else "shopid"
    sel = s.find("select", {"name": name})
    items = (
        [{"id": int(o["value"]), "name": txt(o)} for o in sel.find_all("option") if o.get("value", "").isdigit()]
        if sel
        else []
    )
    return {what: items}
