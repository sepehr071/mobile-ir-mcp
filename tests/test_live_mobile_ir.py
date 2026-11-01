import pytest

pytestmark = [pytest.mark.live, pytest.mark.anyio]

S26 = 41113  # Samsung Galaxy S26 Ultra


async def test_live_search(client):
    r = (await client.call_tool("mi_search", {"query": "galaxy s26"})).structured_content
    assert any(x["id"] == S26 for x in r["results"]), r


async def test_live_find(client):
    r = (
        await client.call_tool(
            "mi_find",
            {
                "brand": "Samsung",
                "min_price": 20_000_000,
                "max_price": 100_000_000,
                "sort": "price",
                "descending": False,
            },
        )
    ).structured_content
    prices = [p["price_toman"] for p in r["phones"]]
    assert r["found"] > 0 and prices == sorted(prices) and all(20_000_000 <= p <= 100_000_000 for p in prices), r


async def test_live_find_all_brands_camera(client):
    r = (await client.call_tool("mi_find", {"min_camera_mp": 100, "limit": 5})).structured_content
    assert len(r["phones"]) == 5 and all(p["camera_mp"] >= 100 for p in r["phones"]), r


async def test_live_phone(client):
    r = (await client.call_tool("mi_phone", {"id": S26})).structured_content
    assert "S26 Ultra" in r["name"] and "صفحه نمایش" in r["specs"] and "باطری" in r["specs"]
    assert r["rating"] > 0 and r["highlights"]


async def test_live_shop_prices_and_history(client):
    r = (await client.call_tool("mi_shop_prices", {"id": S26})).structured_content
    assert r["offer_count"] > 0 and r["offers"][0]["price_toman"] > 0
    h = (await client.call_tool("mi_price_history", {"id": S26, "period": "3months"})).structured_content
    assert h["shops"] and h["daily_lowest_toman"]


async def test_live_market_prices(client):
    r = (
        await client.call_tool("mi_market_prices", {"brand": "Samsung", "sort": "price", "descending": False})
    ).structured_content
    assert r["total_rows"] and r["rows"] and r["rows"][0]["name"].startswith("Samsung")


async def test_live_used_search_and_ad(client):
    r = (await client.call_tool("mi_used_search", {"days": -1})).structured_content
    assert r["total"] > 0 and r["ads"]
    ad = (await client.call_tool("mi_used_ad", {"ad_id": r["ads"][0]["ad_id"]})).structured_content
    assert ad["title"] and ad["url"]


async def test_live_sim_search(client):
    r = (
        await client.call_tool(
            "mi_sim_search", {"operator": "irancell", "number": "1111", "match": "endswith", "days": -1}
        )
    ).structured_content
    assert r["total"] > 0 and all(n["number"].endswith("1111") and n["operator"] == "ایرانسل" for n in r["numbers"]), r


async def test_live_options(client):
    brands = (await client.call_tool("mi_options", {"what": "brands"})).structured_content["brands"]
    assert any(b["name"] == "Samsung" and b["id"] == 2 for b in brands)
    shops = (await client.call_tool("mi_options", {"what": "shops"})).structured_content["shops"]
    assert shops


async def test_live_compare_and_comments(client):
    r = (await client.call_tool("mi_compare", {"ids": [S26, 41115], "sections": ["body"]})).structured_content
    weights = next(x for x in r["rows"] if x["label"] == "وزن")["values"]
    assert [p["id"] for p in r["phones"]] == [S26, 41115] and weights[0] != weights[1]
    c = (await client.call_tool("mi_comments", {"id": S26, "limit": 5, "sort": "helpful"})).structured_content
    assert c["total"] > 0 and c["comments"] and c["comments"][0]["text"]


async def test_live_articles(client):
    news = (await client.call_tool("mi_articles", {})).structured_content
    assert news["total"] > 1000 and news["articles"][0]["source"] == "news"
    art = (await client.call_tool("mi_article", {"source": "news", "id": news["articles"][0]["id"]})).structured_content
    assert art["title"] and art["total_chars"] > 200
    rev = (await client.call_tool("mi_articles", {"source": "reviews"})).structured_content
    first = rev["articles"][0]
    text = (
        await client.call_tool(
            "mi_article", {"source": "review", "id": first["id"], "phone_id": first["phone_id"], "max_chars": 800}
        )
    ).structured_content
    assert len(text["text"]) == 800 and text["total_chars"] > 800
    rel = (await client.call_tool("mi_articles", {"phone_id": S26})).structured_content
    assert any(a["source"] == "intro" for a in rel["articles"])


async def test_live_shop_brand_and_sim_summary(client):
    shop = (await client.call_tool("mi_shop", {"shop_id": 128})).structured_content
    assert shop["shop"] and shop["total_phones"] > 20 and shop["phones"][0]["price_toman"] > 0
    b = (await client.call_tool("mi_brand_models", {"brand": "Samsung", "page": 2})).structured_content
    assert b["total"] > 80 and len(b["models"]) == 40
    sims = (await client.call_tool("mi_sim_summary", {})).structured_content["operators"]
    assert sims["mci"]["ads"] > sims["shatel"]["ads"] and sims["mci"]["cheapest"]


async def test_live_tablet_watch_and_paging(client):
    t = (await client.call_tool("mi_find", {"kind": "tablet", "brand": "Apple", "limit": 3})).structured_content
    assert t["phones"] and all(p["kind"] == "tablet" for p in t["phones"])
    w = (await client.call_tool("mi_market_prices", {"kind": "smartwatch"})).structured_content
    assert w["total_rows"] > 0 and w["rows"]
    p1 = (await client.call_tool("mi_used_search", {"days": -1, "page": 1})).structured_content
    p2 = (await client.call_tool("mi_used_search", {"days": -1, "page": 2})).structured_content
    assert not {a["ad_id"] for a in p1["ads"]} & {a["ad_id"] for a in p2["ads"]}
    s1 = (await client.call_tool("mi_sim_search", {"days": 30, "page": 1})).structured_content
    s2 = (await client.call_tool("mi_sim_search", {"days": 30, "page": 2})).structured_content
    assert s2["numbers"] and not {a["sim_id"] for a in s1["numbers"]} & {a["sim_id"] for a in s2["numbers"]}
