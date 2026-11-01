import json

import httpx
import pytest
from conftest import fixture, page

pytestmark = pytest.mark.anyio

TOOLS = {
    "mi_search",
    "mi_find",
    "mi_phone",
    "mi_shop_prices",
    "mi_price_history",
    "mi_market_prices",
    "mi_used_search",
    "mi_used_ad",
    "mi_sim_search",
    "mi_options",
    "mi_compare",
    "mi_comments",
    "mi_articles",
    "mi_article",
    "mi_shop",
    "mi_brand_models",
    "mi_sim_summary",
}


def raw(name: str):
    """Route handler serving a saved file as the body."""
    return page(name)


async def test_all_tools_are_read_only_and_described(client):
    tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == TOOLS
    for t in tools:
        assert t.annotations.read_only_hint is True, t.name
        assert t.title and t.description.splitlines()[0].endswith("."), t.name
        assert "title" not in str(t.input_schema.get("properties", {}).values()), t.name


async def test_search_lists_models(client, api):
    api["/phones/ajaxphonesearch.aspx"] = fixture("search.json")
    r = (await client.call_tool("mi_search", {"query": "galaxy s26"})).structured_content
    assert r["count"] == 4 and r["results"][0] == {"id": 41113, "name": "Samsung Galaxy S26 Ultra", "kind": "phone"}
    req = api.calls[0]
    assert req.url.params["q"] == "galaxy s26" and req.headers["x-requested-with"] == "XMLHttpRequest"


async def test_find_builds_the_finder_query_and_parses_cards(client, api):
    api["/brands/index.aspx"] = raw("brands.html")
    api["/phones/finder.aspx"] = raw("finder.html")
    r = (
        await client.call_tool(
            "mi_find",
            {"brand": "samsung", "min_price": 40_000_000, "max_price": 100_000_000, "sort": "price", "limit": 2},
        )
    ).structured_content
    assert r["shown"] == 2 and r["found"] >= 2
    first = r["phones"][0]
    assert first["id"] and first["brand"] and first["kind"] == "phone" and first["rating"]
    q = [c for c in api.calls if c.url.path.endswith("finder.aspx")][0].url.params
    assert q.get_list("brands") == ["2"] and q["minprice"] == "40000" and q["maxprice"] == "100000"
    assert q["withprice"] == "true" and q["sort"] == "price" and q.get_list("status") == ["0", "1"]


async def test_find_unknown_brand_is_a_clear_error(client, api):
    api["/brands/index.aspx"] = raw("brands.html")
    r = await client.call_tool("mi_find", {"brand": "nosuchbrand"})
    assert r.is_error and "mi_options" in r.content[0].text


async def test_phone_specs(client, api):
    api["/phones/specifications-41113-x.aspx"] = raw("spec.html")
    r = (await client.call_tool("mi_phone", {"id": 41113, "sections": ["battery", "display"]})).structured_content
    assert r["name"].endswith("Samsung Galaxy S26 Ultra") and r["kind"] == "phone"
    assert r["price_toman"] == {"lowest": 249980000, "highest": 518500000}
    assert r["rating"] == 7 and r["votes"] == 248
    assert set(r["specs"]) == {"صفحه نمایش", "باطری"}
    assert r["specs"]["باطری"]["نوع"] == "Li-Ion 5000 mAh"
    assert r["highlights"]["دوربین"].startswith("200")


async def test_phone_unknown_id(client, api):
    api["/phones/specifications-1-x.aspx"] = lambda request: httpx.Response(200, text="<html><body>x</body></html>")
    r = await client.call_tool("mi_phone", {"id": 1})
    assert r.is_error and "no product" in r.content[0].text


async def test_shop_prices_sorted_with_shop_ids(client, api):
    api["/phones/shops-41045-x.aspx"] = raw("shops.html")
    r = (await client.call_tool("mi_shop_prices", {"id": 41045})).structured_content
    assert r["offer_count"] == 2 and r["name"].endswith("Redmi Note 15 Pro+")
    a, b = r["offers"]
    assert (a["shop"], a["shop_id"], a["price_toman"]) == ("فروشگاه الزهرا", 9958, 189980000)
    assert b["price_toman"] == 191000000 and b["shop_id"] == 128 and b["city"] == "اصفهان"
    assert "&#" not in a["note"]


async def test_price_history_summarises_shops_and_daily_lowest(client, api):
    api["/phones/pricechartjson.aspx"] = fixture("pricechart.json")
    r = (await client.call_tool("mi_price_history", {"id": 41045, "period": "3months"})).structured_content
    assert api.calls[0].url.params["rangeIndex"] == "2" and api.calls[0].url.params["shopId"] == "0"
    shop = next(iter(r["shops"].values()))
    assert shop["points"] == 6 and shop["min"] <= shop["first"]["price"] <= shop["max"]
    assert r["daily_lowest_toman"] == sorted(r["daily_lowest_toman"])


async def test_market_prices_rows(client, api):
    api["/brands/index.aspx"] = raw("brands.html")
    api["/phones/prices.aspx"] = raw("prices.html")
    r = (
        await client.call_tool("mi_market_prices", {"brand": "Xiaomi", "province": 5, "sort": "price"})
    ).structured_content
    assert r["total_rows"] == 448 and len(r["rows"]) == 4
    row = r["rows"][0]
    assert row["id"] and row["shop"] and row["price_toman"] and row["updated"].startswith("20")
    q = api.calls[-1].url.params
    assert q["brandid"] == "117" and q["provinceid"] == "5" and q["sort"] == "price" and q["dir"] == "desc"


async def test_used_search_parses_ads_and_strips_contact_data(client, api):
    api["/usedphones/index.aspx"] = raw("used.html")
    r = (await client.call_tool("mi_used_search", {"condition": "new", "max_price": 300_000_000})).structured_content
    assert r["total"] == 42 and len(r["ads"]) == 4
    ad = next(a for a in r["ads"] if a.get("price_toman"))
    assert ad["ad_id"] and ad["phone_id"] and ad["condition"] and ad["city"]
    assert any(a.get("price_note") for a in r["ads"])  # 'highest offer' style prices stay as text
    q = api.calls[0].url.params
    assert q["status"] == "1" and q["pricetype"] == "1" and q["price"] == "300000000" and q["all_brands"] == "on"
    assert "09" not in json.dumps(r, ensure_ascii=False).replace("2026", "")  # no phone numbers in the output


async def test_used_ad_detail_omits_seller(client, api):
    api["/usedphones/info.aspx"] = raw("used_ad.html")
    r = (await client.call_tool("mi_used_ad", {"ad_id": 320713})).structured_content
    assert r["price_toman"] == 225000000 and r["market_price_toman"] == 351000000 and r["box"] == "دارد"
    assert r["phone_id"] == 40553 and r["other_ads_same_model"] and r["shops_selling_model"]
    text = json.dumps(r, ensure_ascii=False)
    assert "seller" not in text and "09000000000" not in text


async def test_sim_search_query_and_rows(client, api):
    api["/simcard/index.aspx"] = raw("sim.html")
    r = (
        await client.call_tool(
            "mi_sim_search",
            {"operator": "rightel", "number": "1111", "match": "endswith", "round_only": True, "max_price": 5_000_000},
        )
    ).structured_content
    assert r["total"] == 2346 and len(r["numbers"]) == 4
    n = r["numbers"][0]
    assert n["sim_id"] and len(n["number"]) == 11 and n["operator"] and n["url"].endswith(str(n["sim_id"]))
    q = api.calls[0].url.params
    assert q.get_list("prenums") == ["26", "28", "33", "41"] and q["numbercondition"] == "endswith"
    assert q["number"] == "1111" and q["round"] == "1" and q["pricetype"] == "1" and q["price"] == "5000000"
    assert "09000000000" not in json.dumps(r)


async def test_options(client, api):
    api["/brands/index.aspx"] = raw("brands.html")
    api["/phones/prices.aspx"] = raw("prices.html")
    brands = (await client.call_tool("mi_options", {"what": "brands"})).structured_content["brands"]
    assert brands[0] == {"id": 7, "slug": "apple", "name": "Apple", "name_fa": "اپل"}
    shops = (await client.call_tool("mi_options", {"what": "shops"})).structured_content["shops"]
    assert {"id": 128, "name": "موبایل جوان"} in shops
    provinces = (await client.call_tool("mi_options", {"what": "provinces"})).structured_content["provinces"]
    assert {"id": 1, "name": "تهران"} in provinces
    ops = (await client.call_tool("mi_options", {"what": "operators"})).structured_content["operators"]
    assert "0912" in ops["mci"]


async def test_http_error_becomes_tool_error(client, api):
    api["/phones/ajaxphonesearch.aspx"] = lambda request: httpx.Response(403, text="no")
    r = await client.call_tool("mi_search", {"query": "x"})
    assert r.is_error and "403" in r.content[0].text


async def test_refuses_other_hosts():
    from mobile_ir_mcp import http

    with pytest.raises(http.ApiError):
        await http.fetch("https://example.com/")


async def test_request_headers_match_the_sites_frontend(api):
    from mobile_ir_mcp import http

    api["/x.aspx"] = {}
    sent = {}
    for kind in ("page", "ajax", "json"):
        await http.fetch("/x.aspx", {"k": kind}, kind=kind)
        sent[kind] = api.calls[-1].headers
    assert sent["page"]["accept"].startswith("text/html") and "x-requested-with" not in sent["page"]
    assert (sent["page"]["sec-fetch-mode"], sent["page"]["sec-fetch-dest"]) == ("navigate", "document")
    assert sent["ajax"]["accept"] == "text/javascript, text/html, application/xml, text/xml, */*"
    assert sent["json"]["accept"] == "*/*"
    for k in ("ajax", "json"):
        h = sent[k]
        assert h["x-requested-with"] == "XMLHttpRequest" and h["referer"] == "https://www.mobile.ir/"
        assert (h["sec-fetch-site"], h["sec-fetch-mode"], h["sec-fetch-dest"]) == ("same-origin", "cors", "empty")
        assert h["priority"] == "u=1, i" and "origin" not in h
