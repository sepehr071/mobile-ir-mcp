import httpx
import pytest
from conftest import FIXTURES, page

pytestmark = pytest.mark.anyio


async def test_compare_lines_up_specs_and_drops_equal_rows(client, api):
    api["/phones/specifications-1-x.aspx"] = page("spec.html")
    api["/phones/specifications-2-x.aspx"] = page("spec.html")
    r = (await client.call_tool("mi_compare", {"ids": [1, 2]})).structured_content
    assert [p["id"] for p in r["phones"]] == [1, 2] and r["rows"] == []  # identical sheets: nothing differs
    full = await client.call_tool("mi_compare", {"ids": [1, 2], "only_differences": False, "sections": ["battery"]})
    row = full.structured_content["rows"][0]
    assert row["section"] == "باطری" and row["label"] == "نوع" and row["values"] == ["Li-Ion 5000 mAh"] * 2


async def test_compare_keeps_differing_rows(client, api):
    body = (FIXTURES / "spec.html").read_text(encoding="utf-8")
    api["/phones/specifications-1-x.aspx"] = lambda request: httpx.Response(200, text=body)
    api["/phones/specifications-2-x.aspx"] = lambda request: httpx.Response(
        200, text=body.replace("5000 mAh", "4000 mAh")
    )
    r = (await client.call_tool("mi_compare", {"ids": [1, 2]})).structured_content
    assert [x["values"] for x in r["rows"]] == [["Li-Ion 5000 mAh", "Li-Ion 4000 mAh"]]


async def test_comments_sorted_and_without_author(client, api):
    api["/phones/comments-41113-x.aspx"] = page("comments.html")
    r = (await client.call_tool("mi_comments", {"id": 41113, "limit": 10})).structured_content
    ids = [c["comment_id"] for c in r["comments"]]
    assert ids == sorted(ids, reverse=True) and r["total"] == 4
    assert any(c.get("reply_to") for c in r["comments"])
    assert all(c["date"].startswith("140") and c["text"] for c in r["comments"])
    assert "user" not in str(r["comments"]) and "profile.aspx" not in str(r)
    oldest = (await client.call_tool("mi_comments", {"id": 41113, "sort": "oldest", "limit": 1})).structured_content
    assert oldest["comments"][0]["comment_id"] == min(ids) and oldest["shown"] == 1


async def test_articles_news_reviews_and_phone(client, api):
    api["/news/index.aspx"] = page("news_list.html")
    r = (await client.call_tool("mi_articles", {})).structured_content
    assert r["total"] == 6003 and len(r["articles"]) == 3
    assert r["articles"][0]["source"] == "news" and r["articles"][0]["id"] and r["articles"][0]["summary"]
    api["/phones/reviews.aspx"] = page("reviews_list.html")
    r = (await client.call_tool("mi_articles", {"source": "reviews", "phone_id": 40975})).structured_content
    first = r["articles"][0]
    assert first["source"] == "review" and first["phone_id"] and first["id"]
    assert api.calls[-1].url.params["phoneid"] == "40975"
    api["/phones/related-41113-x.aspx"] = page("related.html")
    r = (await client.call_tool("mi_articles", {"phone_id": 41113})).structured_content
    assert {a["source"] for a in r["articles"]} == {"news", "intro"}
    api["/news/search.aspx"] = page("news_list.html")
    await client.call_tool("mi_articles", {"query": "سامسونگ"})
    assert api.calls[-1].url.params["terms"] == "سامسونگ"


async def test_article_text_and_offset(client, api):
    api["/news/view-6552-x.aspx"] = page("article.html")
    r = (await client.call_tool("mi_article", {"source": "news", "id": 6552, "max_chars": 500})).structured_content
    assert r["title"].startswith("گزارش مالی") and r["total_chars"] > 500 and len(r["text"]) == 500
    more = (await client.call_tool("mi_article", {"source": "news", "id": 6552, "offset": 500})).structured_content
    assert more["text"] and more["text"] != r["text"]
    bad = await client.call_tool("mi_article", {"source": "review", "id": 1})
    assert bad.is_error and "phone_id" in bad.content[0].text


async def test_shop_profile_and_catalog(client, api):
    api["/brands/index.aspx"] = page("brands.html")
    api["/directory/view.aspx"] = page("shop.html")
    r = (await client.call_tool("mi_shop", {"shop_id": 128, "brand": "samsung"})).structured_content
    assert r["shop"] == "موبایل جوان" and r["province"] == "اصفهان" and r["total_phones"] == 119
    assert len(r["phones"]) == 3 and all(p["id"] and p["price_toman"] and p["name"] for p in r["phones"])
    assert "0311" not in str(r) and "[phone]" in r["about"]
    q = api.calls[-1].url.params
    assert q["id"] == "128" and q["brandid"] == "2"


async def test_brand_models_pages(client, api):
    api["/brands/index.aspx"] = page("brands.html")
    api["/brands/2-x.aspx"] = page("brand.html")
    r = (
        await client.call_tool(
            "mi_brand_models", {"brand": "samsung", "kind": "tablet", "only_priced": True, "page": 1}
        )
    ).structured_content
    assert r["brand_id"] == 2 and r["total"] == 106 and len(r["models"]) == 3
    q = api.calls[-1].url.params
    assert q["product_class"] == "Tablet" and q["withprice"] == "true" and q["sort"] == "date"


async def test_sim_summary_per_operator(client, api):
    api["/simcard/index.aspx"] = page("sim.html")
    r = (await client.call_tool("mi_sim_summary", {})).structured_content
    assert set(r["operators"]) == {"mci", "irancell", "rightel", "taliya", "shatel"}
    assert r["operators"]["mci"]["ads"] == 2346 and r["operators"]["mci"]["cheapest"]
    priced = [c for c in api.calls if c.url.params.get("pricetype") == "2"]
    assert len(priced) == 5 and priced[0].url.params["price"] == "10000" and priced[0].url.params["dir"] == "asc"


async def test_find_rating_and_year_filters(client, api):
    api["/brands/index.aspx"] = page("brands.html")
    api["/phones/finder.aspx"] = page("finder.html")
    every = (await client.call_tool("mi_find", {})).structured_content["phones"]
    top = (await client.call_tool("mi_find", {"min_rating": 10, "min_year": 2100})).structured_content
    assert every and top["phones"] == [] and top["found"] >= len(every)


async def test_missing_article_is_a_clear_error(client, api):
    api["/news/view-1-x.aspx"] = lambda request: httpx.Response(200, text="<html><body>x</body></html>")
    r = await client.call_tool("mi_article", {"source": "news", "id": 1})
    assert r.is_error and "no article" in r.content[0].text
