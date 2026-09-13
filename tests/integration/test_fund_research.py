"""Read-only stage-06 report indexes do not cause external refreshes."""

def test_sector_fund_and_market_review_reads_are_local_only(client):
    sector = client.get("/api/sectors/CSI300/research")
    fund = client.get("/api/funds/fund-a/research")
    review = client.get("/api/market-reviews")
    assert sector.status_code == fund.status_code == review.status_code == 200
    assert sector.json()["local_only"] is True
    assert fund.json()["local_only"] is True
    assert review.json()["local_only"] is True
