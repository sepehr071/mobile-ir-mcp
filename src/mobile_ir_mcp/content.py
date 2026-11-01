"""Mobile.ir round-2 tools: compare, user comments, news and reviews, shop catalogs, brand lists, SIM market summary."""

from __future__ import annotations

import asyncio
import re
from typing import Annotated, Any, Literal

from bs4.element import NavigableString, Tag
from pydantic import Field

from .http import ApiError, fetch
from .mobile import PATH, Kind, PhoneId, _brand_id, _not_found, mi_phone, mi_sim_search
from .parse import cards, clip, num, redact, soup, total, txt
from .registry import tool

SITE = "https://www.mobile.ir"
SORTS = Literal["rating", "date", "price", "camera", "weight", "name"]
SITE_SORT = {
    "rating": "rating",
    "date": "date",
    "price": "price",
    "camera": "camerares",
    "weight": "weight",
    "name": "model",
}


@tool("Compare phones side by side")
async def mi_compare(
    ids: Annotated[
        list[int], Field(description="2 to 4 product ids from mi_search or mi_find.", min_length=2, max_length=4)
    ],
    only_differences: Annotated[
        bool, Field(description="Keep only the spec rows where the products differ (default); false returns every row.")
    ] = True,
    sections: Annotated[
        list[Literal["general", "body", "display", "memory", "camera", "connectivity", "features", "battery", "misc"]]
        | None,
        Field(description="Limit to these spec sections, e.g. ['display','battery']; default all."),
    ] = None,
) -> dict[str, Any]:
    """Compare 2 to 4 phones, tablets or watches on Mobile.ir: price range, rating and each spec row side by side.

    Use it after mi_search or mi_find when the user is choosing between models. Values are listed in the same order as
    `phones`. By default only rows that differ are returned, so the answer stays short; ask for `sections` to focus.
    """
    sheets = await asyncio.gather(*(mi_phone(i, sections) for i in ids))
    phones = [
        {"id": s["id"], "name": s["name"], "price_toman": s.get("price_toman"), "rating": s.get("rating")}
        for s in sheets
    ]
    keys: dict[tuple[str, str], None] = {}
    for s in sheets:
        for sec, items in s["specs"].items():
            for label in items:
                keys[(sec, label)] = None
    rows = []
    for sec, label in keys:
        vals = [s["specs"].get(sec, {}).get(label) for s in sheets]
        if only_differences and len({v for v in vals}) == 1:
            continue
        rows.append({"section": sec, "label": label, "values": vals})
    return {"phones": phones, "rows": rows}


@tool("User comments on a phone")
async def mi_comments(
    id: PhoneId,
    sort: Annotated[
        Literal["newest", "helpful", "oldest"], Field(description="'helpful' puts the most liked comments first.")
    ] = "newest",
    limit: Annotated[int, Field(description="Comments to return.", ge=1, le=50)] = 15,
) -> dict[str, Any]:
    """Read what Mobile.ir users wrote about a phone, tablet or watch: comment text, date (Persian calendar), likes.

    Use it for real-world opinions (battery, camera, durability) next to mi_phone's rating. Comments are unverified
    user content; author names are not returned. `reply_to` is the comment id a comment answers.
    """
    s = soup(await fetch(f"/phones/comments-{id}-x.aspx"))
    if s.select_one(".user-comments") is None:
        raise _not_found("product")
    out = []
    for c in s.select(".user-comments .comment"):
        body = c.select_one(".padd")
        reply = body.select_one(".inreplyto") if body else None
        reply_to = None
        if reply is not None:
            m = re.search(r"loadcomment\((\d+)", str(reply))
            reply_to = int(m.group(1)) if m else None
            reply.decompose()
        up = num(txt(c.select_one(".footer .voteup > span"))) or 0
        down = num(txt(c.select_one(".footer .votedown > span"))) or 0
        out.append(
            {
                "comment_id": int(c["id"].split("_")[1]),
                "date": txt(c.select_one("h3 b")),
                "text": clip(redact(txt(body)), 600),
                "likes": up,
                "dislikes": down,
                "reply_to": reply_to,
            }
        )
    if sort == "newest":
        out.sort(key=lambda x: -x["comment_id"])
    elif sort == "oldest":
        out.sort(key=lambda x: x["comment_id"])
    else:
        out.sort(key=lambda x: (-(x["likes"] - x["dislikes"]), -x["comment_id"]))
    head = s.select_one(".comments")
    return {"id": id, "total": len(out) or num(txt(head)) or 0, "shown": min(limit, len(out)), "comments": out[:limit]}


def _items(s) -> list[dict[str, Any]]:
    """News and review list rows: `h3.innertitle` (date + link) followed by a `.newslist` snippet."""
    out = []
    for h in s.select("h3.innertitle"):
        a = h.find("a")
        if a is None:
            continue
        href = a.get("href", "")
        nid = re.search(r"/news/view-(\d+)-", href)
        rev = re.search(r"/review-(\d+)-[^?]*\?revid=(\d+)", href)
        body = h.find_next_sibling(class_="newslist")
        snippet = ""
        if body is not None:
            snippet = txt(body).replace("ادامه ...", "").strip()
        item: dict[str, Any] = {
            "title": txt(a),
            "date": txt(h.find("span")),
            "summary": clip(snippet, 220),
        }
        if nid:
            item.update(source="news", id=int(nid.group(1)))
        elif rev:
            item.update(source="review", id=int(rev.group(2)), phone_id=int(rev.group(1)))
        elif "/intro-" in href:
            item.update(source="intro", id=int(re.search(r"intro-(\d+)-", href).group(1)))
        else:
            continue
        out.append(item)
    return out


@tool("Mobile.ir news and expert reviews")
async def mi_articles(
    source: Annotated[
        Literal["news", "reviews"], Field(description="'news' = news and features; 'reviews' = expert phone reviews.")
    ] = "news",
    query: Annotated[
        str | None, Field(description="Words to search in the news (news only), e.g. 'سامسونگ'.", max_length=60)
    ] = None,
    phone_id: Annotated[
        int | None,
        Field(description="Only articles about this product id: related news and intro (news) or its reviews.", ge=1),
    ] = None,
    brand: Annotated[str | None, Field(description="Brand name or id for reviews (see mi_options('brands')).")] = None,
    kind: Annotated[Kind, Field(description="Product class for reviews.")] = "phone",
    page: Annotated[int, Field(description="Page number, from 1 (20 news or 40 reviews per page).", ge=1)] = 1,
) -> dict[str, Any]:
    """List the news and the expert reviews on Mobile.ir, newest first, optionally for one phone or brand.

    Use it for 'is there a review of X', 'latest news about Samsung' or 'what is new'. Read one result with
    mi_article, passing back its `source`, `id` and (for reviews) `phone_id`. News search returns at most 100 hits.
    """
    if source == "reviews":
        q: dict[str, Any] = {"page": page}
        if phone_id:
            q["phoneid"] = phone_id
        if brand:
            q["brandid"] = await _brand_id(brand)
        s = soup(await fetch(f"/{PATH[kind]}/reviews.aspx", q))
    elif phone_id:
        s = soup(await fetch(f"/phones/related-{phone_id}-x.aspx"))
        items = []
        for a in s.select(".panel__content .content-link-list li > a:last-of-type"):
            href = a.get("href", "")
            nid = re.search(r"/news/view-(\d+)-", href)
            intro = re.search(r"/intro-(\d+)-", href)
            if nid:
                items.append({"source": "news", "id": int(nid.group(1)), "title": txt(a)})
            elif intro:
                items.append({"source": "intro", "id": int(intro.group(1)), "title": txt(a)})
        return {"count": len(items), "page": 1, "articles": items}
    elif query:
        s = soup(await fetch("/news/search.aspx", {"terms": query, "page": page}))
    else:
        s = soup(await fetch("/news/index.aspx", {"page": page}))
    items = _items(s)
    return {"total": total(txt(s.select_one(".paginationbox .info"))) or len(items), "page": page, "articles": items}


def _long_text(box: Tag) -> tuple[str, int]:
    """Readable text of an article body (headings marked with '##', list items with '-'), and its image count."""
    images = len(box.find_all("img"))
    for t in box(["script", "style"]):
        t.decompose()
    lines: list[str] = []

    def walk(el: Tag) -> None:
        buf = ""
        for ch in el.children:
            if isinstance(ch, NavigableString):
                buf += str(ch)
            elif ch.name in ("p", "div", "ul", "ol", "table", "tr", "h1", "h2", "h3", "h4", "li", "br"):
                if buf.strip():
                    lines.append(buf.strip())
                buf = ""
                if ch.name == "br":
                    continue
                if ch.name in ("h1", "h2", "h3", "h4"):
                    lines.append("## " + txt(ch))
                elif ch.name == "li":
                    lines.append("- " + txt(ch))
                elif ch.name == "tr":
                    lines.append(" | ".join(txt(c) for c in ch.find_all(["th", "td"])))
                elif ch.name in ("div", "ul", "ol", "table"):
                    walk(ch)
                else:
                    t = txt(ch)
                    if t:
                        lines.append(t)
            else:
                buf += ch.get_text(" ")
        if buf.strip():
            lines.append(buf.strip())

    walk(box)
    text = "\n".join(re.sub(r"\s+", " ", ln).strip() for ln in lines if ln.strip())
    return text, images


@tool("Read a news article or review")
async def mi_article(
    source: Annotated[
        Literal["news", "review", "intro"],
        Field(description="From mi_articles: 'news', 'review' or 'intro' (for 'intro' the id is the product id)."),
    ],
    id: Annotated[int, Field(description="Article id (news id, review id, or product id for 'intro').", ge=1)],
    phone_id: Annotated[
        int | None, Field(description="Product id the review belongs to (required for 'review').", ge=1)
    ] = None,
    offset: Annotated[int, Field(description="Start at this character, to read a long review in parts.", ge=0)] = 0,
    max_chars: Annotated[int, Field(description="Characters to return.", ge=500, le=12000)] = 6000,
) -> dict[str, Any]:
    """Read the text of one Mobile.ir news item, expert review or product introduction (Persian, images dropped).

    Get `source`, `id` and `phone_id` from mi_articles. Long reviews run to tens of thousands of characters: the answer
    says `total_chars`, so call again with `offset` to continue. Press-release style text: verify claims before quoting.
    """
    if source == "news":
        path, params = f"/news/view-{id}-x.aspx", None
    elif source == "intro":
        path, params = f"/phones/intro-{id}-x.aspx", None
    else:
        if not phone_id:
            raise ApiError("phone_id is required for a review (mi_articles gives it).")
        path, params = f"/phones/review-{phone_id}-x.aspx", {"revid": id}
    s = soup(await fetch(path, params))
    box = s.select_one(".longtext")
    title = txt(s.select_one("h3.innertitle")) or txt(s.title).rpartition("|")[0].partition("|")[2].strip()
    if box is None or not title:
        raise _not_found("article")
    text, images = _long_text(box)
    return {
        "source": source,
        "id": id,
        "title": title,
        "total_chars": len(text),
        "offset": offset,
        "images_dropped": images,
        "text": text[offset : offset + max_chars],
    }


@tool("A shop's phone price list")
async def mi_shop(
    shop_id: Annotated[int, Field(description="Shop id from mi_options('shops') or mi_shop_prices.", ge=1)],
    brand: Annotated[str | None, Field(description="Only this brand (name or id).")] = None,
    name_contains: Annotated[str | None, Field(description="Part of the model name.", max_length=40)] = None,
    page: Annotated[int, Field(description="Page number, from 1 (20 phones per page).", ge=1)] = 1,
) -> dict[str, Any]:
    """Show one Mobile.ir shop's profile and the phones it lists with their current prices in Toman.

    Use it for 'what does shop X sell' or to compare a shop's prices for a brand. For one model across all shops use
    mi_shop_prices. Phone numbers and e-mail of the shop are not returned; `shop_url` shows them on the site.
    """
    q: dict[str, Any] = {"id": shop_id, "page": page}
    if brand:
        q["brandid"] = await _brand_id(brand)
    if name_contains:
        q["terms"] = name_contains
    s = soup(await fetch("/directory/view.aspx", q))
    head = s.select_one(".panel1 > .head h2")
    if head is None or s.select_one(".directory-details") is None:
        raise _not_found("shop")
    det = s.select_one(".directory-details")
    labels = {txt(lb): lb.find_next_sibling("div") for lb in det.find_all("label")}
    site = s.select_one(".contact-info .website a")
    phones = []
    for p in s.select(".shopphones .phone"):
        a = p.select_one("a[href*='specifications-']")
        m = re.search(r"specifications-(\d+)-", a["href"]) if a else None
        dd = {txt(dt): txt(dt.find_next_sibling("dd")) for dt in p.select("dl dt")}
        full = p.select_one(".full")
        note = txt(full) if full else txt(p.select_one(".toggledesc"))
        note = re.sub(r"\(\s*کمتر\s*\)$", "", note).strip()
        phones.append(
            {
                "id": int(m.group(1)) if m else None,
                "brand": txt(p.select_one("a span")).replace(txt(p.select_one("a strong")), "").strip(),
                "name": txt(p.select_one("a strong")),
                "price_toman": num(dd.get("قیمت:", "")),
                "updated": dd.get("به روز رسانی:"),
                "warranty": clip(dd.get("گارانتی:", ""), 80),
                "note": clip(redact(note), 160),
            }
        )
    return {
        "shop_id": shop_id,
        "shop": txt(head),
        "shop_url": f"{SITE}/directory/view.aspx?id={shop_id}",
        "province": txt(labels.get("استان:")),
        "address": clip(redact(txt(labels.get("آدرس:"))), 200),
        "website": site.get("href") if site else None,
        "about": clip(redact(txt(s.select_one(".directory-desc"))), 300),
        "total_phones": total(txt(s.select_one(".paginationbox .info"))) or len(phones),
        "page": page,
        "phones": phones,
    }


@tool("All models of a brand")
async def mi_brand_models(
    brand: Annotated[str, Field(description="Brand name (Samsung, اپل) or id; see mi_options('brands').")],
    kind: Annotated[Literal["all", "phone", "tablet", "smartwatch"], Field(description="Product class.")] = "all",
    sort: Annotated[SORTS, Field(description="Order of the list.")] = "date",
    descending: Annotated[bool, Field(description="Highest / newest first (default).")] = True,
    only_priced: Annotated[bool, Field(description="Only models with a current shop price.")] = False,
    page: Annotated[int, Field(description="Page number, from 1 (40 models per page).", ge=1)] = 1,
) -> dict[str, Any]:
    """Page through every model Mobile.ir lists for one brand (id, rating, year, screen, camera, lowest shop price).

    Use it when mi_find's 100-row cap is too small or you want a brand's full line-up. Narrow by price or camera with
    mi_find instead. Read one model with mi_phone; compare several with mi_compare.
    """
    bid = await _brand_id(brand)
    q = {
        "sort": SITE_SORT[sort],
        "dir": "desc" if descending else "asc",
        "product_class": "All" if kind == "all" else kind.capitalize(),
        "page": page,
    }
    if only_priced:
        q["withprice"] = "true"
    s = soup(await fetch(f"/brands/{bid}-x.aspx", q))
    models = cards(s)
    return {
        "brand_id": bid,
        "total": total(txt(s.select_one(".paginationbox .info"))) or len(models),
        "page": page,
        "models": models,
    }


@tool("SIM number market summary")
async def mi_sim_summary(
    days: Annotated[Literal[2, 7, 30, 90, -1], Field(description="Ads updated within this many days (-1 = all).")] = 30,
    condition: Annotated[Literal["any", "new", "used"], Field(description="New (صفر) or used line.")] = "any",
    plan: Annotated[Literal["any", "postpaid", "prepaid"], Field(description="Permanent or credit lines.")] = "any",
    round_only: Annotated[bool, Field(description="Only 'round' (رند) numbers.")] = False,
    min_price: Annotated[
        int, Field(description="Toman; ignores placeholder prices such as 1 or 5 that old ads carry.", ge=1)
    ] = 10000,
) -> dict[str, Any]:
    """Summarise the numbered-SIM market on Mobile.ir per operator: ad counts and the three cheapest priced numbers.

    Use it for 'how many Irancell numbers are for sale' or 'cheapest Rightel number'. Drill into one operator with
    mi_sim_search. Prices are Toman; many ads have no price (negotiable) and are counted in `ads` only.
    """
    ops = ["mci", "irancell", "rightel", "taliya", "shatel"]
    base = {"days": days, "condition": condition, "plan": plan, "round_only": round_only}
    sem = asyncio.Semaphore(2)

    async def one(op: str, **kw: Any) -> dict[str, Any]:
        async with sem:
            return await mi_sim_search(operator=op, **base, **kw)

    allr = await asyncio.gather(*(one(op) for op in ops))
    priced = await asyncio.gather(*(one(op, min_price=min_price, sort="price", descending=False) for op in ops))
    return {
        "operators": {
            op: {
                "ads": a["total"],
                "priced": p["total"],
                "cheapest": [
                    {"number": n["number"], "price_toman": n["price_toman"], "sim_id": n["sim_id"]}
                    for n in p["numbers"][:3]
                ],
            }
            for op, a, p in zip(ops, allr, priced, strict=True)
        }
    }
