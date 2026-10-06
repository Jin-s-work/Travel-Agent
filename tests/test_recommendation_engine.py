"""Synthetic decision fixtures, never an accuracy evaluation of real venues."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.recommendations.engine import recommend, RankerConfig, WEIGHTS
from src.recommendations.explanations import fallback, validate
from tests.discovery_synthetic import pack, conditions


NOW = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)


def catalog(city="tokyo"):
    data = pack(city)
    rows = []
    for n, item in enumerate(data["places"], 1):
        item.update(place_id=f"place_{city}_{n}", city=city, identity_status="verified", pack_status="approved", synthetic=True)
        for source in item["sources"]:
            source.update(id=f"source_{n}_{source['key']}", status="active", policy_version=data["version"],
                          checked_at=(NOW-timedelta(days=1)).isoformat())
        source_ids = {source["key"]: source["id"] for source in item["sources"]}
        for i, fact in enumerate(item["facts"]):
            fact.update(id=f"fact_{n}_{i}", source_id=source_ids[fact["source_key"]], policy_version=data["version"],
                        checked_at=(NOW-timedelta(days=1)).isoformat(), expires_at=(NOW+timedelta(days=90)).isoformat(),
                        freshness="fresh", usable=True)
        rows.append(item)
    return rows


def snapshot(city="tokyo", **overrides):
    value = {"conditions": conditions(city), "limit": 6, "rating_filter": {"enabled": True, "min_rating": 4.2, "min_count": 200, "apply_to": ["local_discovery"]}}
    value.update(overrides)
    return value


def fact(candidate, field):
    return next(item for item in candidate["facts"] if item["field"] == field)


def all_items(result, kind="local_discovery"):
    return {item["place_id"]: item for group in result["sections"][kind].values() for item in group}


def review(candidate, *, qualified=True, expired=False):
    candidate["review_evidence"] = {
        "state": "available", "checked_at": (NOW-timedelta(hours=2)).isoformat(),
        "expires_at": (NOW+timedelta(days=2) if not expired else NOW-timedelta(seconds=1)).isoformat(),
        "scope_mode": "observed_window", "population_inference_allowed": False, "residency_inference_allowed": False,
        "platform": "synthetic", "aggregate_id": "aggregate_"+candidate["place_id"], "synthetic": True,
        "evaluation": {"decision": "pass" if qualified else "unsupported", "strict_pass": qualified,
                       "reason_codes": [] if qualified else ["CLASSIFICATION_QUALITY_UNVERIFIED"]},
        "metrics": {"local_share_lower_bound": .8, "korean_share_upper_bound": .04},
        "counts": {"observed_record_count": 200, "text_count": 200, "classified_count": 190, "unknown_count": 10, "local_count": 160, "korean_count": 0},
    }


@pytest.mark.parametrize("city,adults", [("tokyo", 4), ("barcelona", 2)])
def test_separate_types_categories_and_exact_weighted_sum(city, adults):
    rows, request = catalog(city), snapshot(city)
    request["conditions"]["party"]["adults"] = adults
    out = recommend(request, rows, now=NOW)
    for kind in ("local_discovery", "landmark"):
        assert out["sections"][kind]["items"]
        for item in out["sections"][kind]["items"]:
            assert item["category"] == "restaurant"
            assert all(check["state"] == "confirmed" for check in item["visit_fit"]["checks"])
            parts = item["score_components"]
            assert sum(Decimal(str(v["weight"])) for v in parts.values()) == 1
            assert item["score"] == round(sum(v["weight"]*v["value"] for v in parts.values()), 6)
            assert "LIVE_AVAILABILITY_NOT_CONFIRMED" in item["important_unknowns"]
    request["conditions"].update(categories=["cafe"], recommendation_types=["local_discovery"])
    result = recommend(request, rows, now=NOW)
    assert [x["category"] for x in result["sections"]["local_discovery"]["items"]] == ["cafe"]
    assert not result["sections"]["landmark"]["items"]


@pytest.mark.parametrize("field", ["max_party", "min_party"])
def test_facility_capacity_never_substitutes_booking_limit(field):
    row = catalog()[0]
    fact(row, field).update(value=None, status="unknown")
    fact(row, "facility_capacity")["value"] = 2500
    result = recommend(snapshot(), [row], now=NOW)
    assert not result["sections"]["local_discovery"]["items"]
    item = result["sections"]["local_discovery"]["needs_confirmation"][0]
    assert field.upper()+"_UNKNOWN" in item["reason_codes"]


def test_closed_and_explicit_party_or_diet_mismatch_fail_unknown_separate():
    rows = catalog()
    unknown = deepcopy(rows[0]); unknown["place_id"] = "unknown"
    fact(unknown, "max_party").update(status="unknown", value=None)
    request = snapshot(); request["conditions"]["required"]["dietary"] = ["nut_free"]
    out = recommend(request, rows + [unknown], now=NOW)
    assert not out["sections"]["local_discovery"]["items"]
    assert "PLACE_CLOSED" in all_items(out)[rows[3]["place_id"]]["reason_codes"]
    assert "MAX_PARTY_MISMATCH" in all_items(out)[rows[2]["place_id"]]["reason_codes"]
    assert "DIETARY_MISMATCH" in all_items(out)[unknown["place_id"]]["reason_codes"]


def test_provisional_closed_report_needs_confirmation_and_future_source_not_used():
    row = catalog()[0]
    fact(row, "closed").update(status="provisional", value=True)
    item = all_items(recommend(snapshot(), [row], now=NOW))[row["place_id"]]
    assert item["eligibility"] == "needs_confirmation"
    assert "BUSINESS_STATUS_UNCONFIRMED" in item["reason_codes"]
    row["sources"][0]["checked_at"] = (NOW+timedelta(days=1)).isoformat()
    item = all_items(recommend(snapshot(), [row], now=NOW))[row["place_id"]]
    assert "MAX_PARTY_UNKNOWN" in item["reason_codes"]


def test_unknown_required_diet_and_child_age_do_not_pass():
    row, request = catalog()[0], snapshot()
    request["conditions"]["required"]["dietary"] = ["gluten_free"]
    request["conditions"]["party"]["children"] = [{"age": None}]
    item = all_items(recommend(request, [row], now=NOW))[row["place_id"]]
    assert item["eligibility"] == "needs_confirmation"
    assert {"DIETARY_UNKNOWN", "CHILD_AGE_UNKNOWN"}.issubset(item["reason_codes"])


def test_child_not_allowed_rejected_even_when_total_seats_sufficient():
    row, request = catalog()[0], snapshot()
    request["conditions"]["party"]["children"] = [{"age": 8}]
    fact(row, "children_rule")["value"] = {"allowed": False}
    item = all_items(recommend(request, [row], now=NOW))[row["place_id"]]
    assert item["eligibility"] == "ineligible" and "CHILDREN_NOT_ALLOWED" in item["reason_codes"]


def test_future_weekly_schedule_and_provisional_are_not_confirmed():
    row = catalog()[0]
    fact(row, "opening_hours").pop("valid_for_date")
    item = all_items(recommend(snapshot(), [row], now=NOW))[row["place_id"]]
    assert item["eligibility"] == "needs_confirmation" and "HOURS_DATE_UNCONFIRMED" in item["reason_codes"]
    fact(row, "opening_hours")["status"] = "provisional"
    item = all_items(recommend(snapshot(), [row], now=NOW))[row["place_id"]]
    assert "HOURS_UNKNOWN" in item["reason_codes"]


def test_overnight_previous_day_intervals_and_last_order():
    row, request = catalog()[0], snapshot()
    fact(row, "opening_hours")["value"]["weekly"] = {"thursday": [{"start": "22:00", "end": "02:00", "end_day_offset": 1, "last_order": "01:00"}], "friday": []}
    request["conditions"]["visit"]["local_time"] = "00:30"
    assert recommend(request, [row], now=NOW)["sections"]["local_discovery"]["items"]
    request["conditions"]["visit"]["local_time"] = "01:30"
    item = all_items(recommend(request, [row], now=NOW))[row["place_id"]]
    assert "AFTER_LAST_ORDER" in item["reason_codes"] and item["eligibility"] == "ineligible"


def test_explicit_exception_closure_and_unparsed_holiday_note():
    row = catalog()[0]
    hours = fact(row, "opening_hours")["value"]
    hours["exceptions"] = [{"date": "2026-11-06", "closed": True}]
    item = all_items(recommend(snapshot(), [row], now=NOW))[row["place_id"]]
    assert "CLOSED_ON_VISIT" in item["reason_codes"]
    hours["exceptions"] = [{"rule": "Closed on variable holidays"}]
    item = all_items(recommend(snapshot(), [row], now=NOW))[row["place_id"]]
    assert "HOURS_EXCEPTION_UNRESOLVED" in item["reason_codes"]


def test_dated_closure_overrides_unconfirmed_future_weekly_schedule():
    row = catalog()[0]
    hours = fact(row, "opening_hours")
    hours.pop("valid_for_date")
    hours["value"]["exceptions"] = [{"date": "2026-11-06", "closed": True}]
    item = all_items(recommend(snapshot(), [row], now=NOW))[row["place_id"]]
    assert "CLOSED_ON_VISIT" in item["reason_codes"] and item["eligibility"] == "ineligible"
    hours["value"]["exceptions"] = []
    closure = deepcopy(fact(row, "closed"))
    closure.update(id="dated-closure", field="exceptional_closures", value={"dates": ["2026-11-06"]})
    row["facts"].append(closure)
    item = all_items(recommend(snapshot(), [row], now=NOW))[row["place_id"]]
    assert {"HOURS_DATE_UNCONFIRMED", "CLOSED_ON_VISIT"}.issubset(item["reason_codes"])
    assert item["eligibility"] == "ineligible"


@pytest.mark.parametrize("field,value,reason", [
    ("break_times", [["11:30", "12:30"]], "DURING_BREAK"),
    ("last_order", {"time": "12:00"}, "AFTER_LAST_ORDER"),
    ("last_entry", "11:45", "AFTER_LAST_ENTRY"),
])
def test_separately_stored_breaks_and_cutoffs(field, value, reason):
    row = catalog()[0]
    extra = deepcopy(fact(row, "opening_hours"))
    extra.update(id="separate-hours-rule", field=field, value=value)
    row["facts"].append(extra)
    item = all_items(recommend(snapshot(), [row], now=NOW))[row["place_id"]]
    assert reason in item["reason_codes"] and item["eligibility"] == "ineligible"


def test_dst_ambiguous_time_requires_confirmation():
    row, request = catalog("barcelona")[0], snapshot("barcelona")
    request["conditions"]["visit"].update(date="2026-10-25", local_time="02:30")
    fact(row, "opening_hours")["valid_for_date"] = "2026-10-25"
    item = all_items(recommend(request, [row], now=NOW))[row["place_id"]]
    assert "VISIT_TIME_AMBIGUOUS" in item["reason_codes"]


@pytest.mark.parametrize("change", ["expired", "policy", "revoked", "future", "conflict"])
def test_fact_policy_time_and_conflict_rechecked_even_if_usable_true(change):
    row = catalog()[0]
    value = fact(row, "max_party")
    if change == "expired": value["expires_at"] = NOW.isoformat()
    elif change == "policy": value["policy_version"] = "withdrawn-policy"
    elif change == "revoked": row["sources"][0]["status"] = "revoked"
    elif change == "future": value["checked_at"] = (NOW+timedelta(days=1)).isoformat()
    else: value["status"] = "conflict"
    item = all_items(recommend(snapshot(), [row], now=NOW))[row["place_id"]]
    assert item["eligibility"] == "needs_confirmation"
    if change != "conflict":
        assert next(f for f in item["facts"] if f["id"] == value["id"])["value"] is None


def test_sqlite_integer_source_booleans_are_supported():
    row = catalog()[0]
    for source in row["sources"]: source.update(read_confirmed=1, display_permitted=1)
    assert recommend(snapshot(), [row], now=NOW)["sections"]["local_discovery"]["items"]


def test_missing_components_null_and_no_weight_redistribution():
    row, request = catalog()[0], snapshot()
    request["conditions"]["origin"] = None
    item = recommend(request, [row], now=NOW)["sections"]["local_discovery"]["insufficient_data"][0]
    assert item["score"] is None and item["score_components"]["movement"]["value"] is None
    assert item["score_components"]["movement"]["weight"] == .2
    assert set(item["score_components"]) == set(WEIGHTS["local_editorial_v1"])
    assert item["movement"]["duration_minutes"] is None


def test_unknown_preference_and_budget_are_not_neutral_zero_prices():
    row, request = catalog()[0], snapshot()
    request["conditions"].update(preferred={"tags": []}, budget=None)
    item = recommend(request, [row], now=NOW)["sections"]["local_discovery"]["insufficient_data"][0]
    assert item["score_components"]["preference"]["value"] is None
    assert item["score_components"]["price"]["value"] is None
    assert item["price_basis"]["amount_max"] == "2000"


@pytest.mark.parametrize("change,reason", [("currency", "PRICE_UNIT_MISMATCH"), ("period", "PRICE_UNIT_MISMATCH"), ("range", "PRICE_RANGE_UNCONFIRMED"), ("outside", "OUTSIDE_BUDGET"), ("unitless", "PRICE_UNKNOWN")])
def test_price_currency_period_and_range_never_silently_convert(change, reason):
    row = catalog()[0]
    price = fact(row, "price")["value"]
    if change == "currency": price["currency"] = "EUR"
    elif change == "period": price["period"] = "day"
    elif change == "range": price["amount_max"] = "4000"
    elif change == "outside": price.update(amount_min="4000", amount_max="5000")
    else: price.pop("basis")
    item = all_items(recommend(snapshot(), [row], now=NOW))[row["place_id"]]
    assert reason in item["reason_codes"]
    assert item["eligibility"] == ("ineligible" if change == "outside" else "needs_confirmation")


def test_group_budget_uses_explicit_party_only_without_children():
    row, request = catalog()[0], snapshot()
    request["conditions"]["budget"].update(basis="group", amount_max="9000")
    assert recommend(request, [row], now=NOW)["sections"]["local_discovery"]["items"]
    request["conditions"]["party"]["children"] = [{"age": 7}]
    item = all_items(recommend(request, [row], now=NOW))[row["place_id"]]
    assert "CHILD_PRICE_UNKNOWN" in item["reason_codes"]


def test_rating_count_is_separate_from_observed_review_text_count():
    row, request = catalog()[0], snapshot()
    review(row)
    fact(row, "rating")["value"]["total_rating_count"] = 199
    item = all_items(recommend(request, [row], now=NOW))[row["place_id"]]
    assert "RATING_BELOW_THRESHOLD" in item["reason_codes"]
    fact(row, "rating")["value"].update(total_rating_count=300, scale=10)
    item = all_items(recommend(request, [row], now=NOW))[row["place_id"]]
    assert "RATING_UNSUPPORTED" in item["reason_codes"]


def test_strict_returns_only_two_qualified_no_editorial_fallback():
    rows, request = catalog(), snapshot()
    request["review_language_filter"] = {"required": True, "apply_only_if_qualified": True, "apply_to": ["local_discovery"]}
    for row in rows: fact(row, "max_party")["value"] = 10; fact(row, "closed")["value"] = False
    review(rows[0]); review(rows[1]); review(rows[2], qualified=False)
    rows[2]["review_evidence"]["counts"]["unknown_count"] = 150
    result = recommend(request, rows, now=NOW)
    assert len(result["sections"]["local_discovery"]["items"]) == 2
    assert all(x["ranker_version"] == "local_observed_v1" for x in all_items(result).values())
    assert "REVIEW_REQUIRED_UNSUPPORTED" in all_items(result)[rows[2]["place_id"]]["reason_codes"]
    assert all_items(result)[rows[2]["place_id"]]["score_components"]["language"]["value"] is None
    assert "INSUFFICIENT_QUALIFIED_LOCAL_DISCOVERY" in result["reason_codes"]


def test_strict_can_apply_to_landmarks_without_changing_iconic_weights():
    row, request = catalog()[0], snapshot()
    request["review_language_filter"] = {"required": True, "apply_to": ["landmark"]}
    result = recommend(request, [row], now=NOW)
    assert result["sections"]["local_discovery"]["items"]
    assert not result["sections"]["landmark"]["items"]
    assert result["sections"]["landmark"]["needs_confirmation"][0]["ranker_version"] == "iconic_v1"


def test_expired_and_translated_unqualified_reviews_not_counted_as_zero_korean():
    row, request = catalog()[0], snapshot()
    request["review_language_filter"] = {"required": True}
    review(row, expired=True)
    item = all_items(recommend(request, [row], now=NOW))[row["place_id"]]
    assert "REVIEW_UNAVAILABLE" in item["reason_codes"] and item["score"] is None
    review(row, qualified=False)
    row["review_evidence"]["evaluation"]["reason_codes"] = ["TRANSLATED_ONLY", "TOO_MANY_UNKNOWN"]
    item = all_items(recommend(request, [row], now=NOW))[row["place_id"]]
    assert {"TRANSLATED_ONLY", "TOO_MANY_UNKNOWN"}.issubset(item["reason_codes"])


def test_observed_quality_requires_authorized_same_platform_cohort():
    row, request = catalog()[0], snapshot()
    request["review_language_filter"] = {"required": True}
    review(row)
    fact(row, "rating")["value"].pop("comparison_cohort")
    item = recommend(request, [row], now=NOW)["sections"]["local_discovery"]["insufficient_data"][0]
    assert item["score"] is None and item["score_components"]["quality"]["value"] is None


def test_explicitly_disabled_rating_filter_not_secretly_reapplied_to_observed_model():
    row, request = catalog()[0], snapshot()
    request["review_language_filter"] = {"required": True}
    request["rating_filter"]["enabled"] = False
    review(row)
    fact(row, "rating")["value"].update(rating=3.9, total_rating_count=199)
    item = recommend(request, [row], now=NOW)["sections"]["local_discovery"]["items"][0]
    assert item["score_components"]["quality"]["value"] == .78
    assert not any(check["field"] == "rating" for check in item["visit_fit"]["checks"])


def test_source_group_duplicates_do_not_count_as_independent_editorial_sources():
    row = catalog()[0]
    fact(row, "local_evidence")["value"]["source_groups"] = ["regional_a", "regional_a", "unread_fake_group"]
    item = recommend(snapshot(), [row], now=NOW)["sections"]["local_discovery"]["items"][0]
    assert item["score_components"]["local_evidence"]["value"] == .5


def test_diversity_dedup_branches_stable_ties_and_no_invalid_fill():
    row, request = catalog()[0], snapshot()
    rows = []
    for n in range(8):
        value = deepcopy(row); value.update(place_id=f"p{n}", chain_id="chain" if n < 3 else None, neighborhood="north" if n < 6 else "south")
        rows.append(value)
    rows.append(deepcopy(rows[0]))
    fact(rows[-2], "closed")["value"] = True
    config = RankerConfig(chain_limit=2, neighborhood_limit=3, category_limit=8)
    first = recommend(request, rows, now=NOW, config=config)
    reverse = recommend(request, list(reversed(rows)), now=NOW, config=config)
    assert first == reverse
    items = first["sections"]["local_discovery"]["items"]
    assert [item["place_id"] for item in items] == ["p0", "p1", "p3", "p6"]
    assert len([item for item in items if item["chain_id"] == "chain"]) == 2
    assert len([item for item in items if item["neighborhood"] == "north"]) == 3
    assert all(item["eligibility"] == "eligible" for item in items)


def test_divergent_duplicate_internal_id_requires_reconciliation_deterministically():
    row = catalog()[0]
    other = deepcopy(row)
    fact(other, "max_party")["value"] = 1
    first = recommend(snapshot(), [row, other], now=NOW)
    reverse = recommend(snapshot(), [other, row], now=NOW)
    assert first == reverse
    assert not first["sections"]["local_discovery"]["items"]
    assert "PLACE_IDENTITY_UNVERIFIED" in all_items(first)[row["place_id"]]["reason_codes"]


def test_user_exclusion_and_radius_do_not_mutate_input():
    row, request = catalog()[0], snapshot()
    original = deepcopy(row)
    request["conditions"]["radius_m"] = 100
    item = all_items(recommend(request, [row], now=NOW))[row["place_id"]]
    assert "OUTSIDE_RADIUS" in item["reason_codes"]
    assert row == original
    row["excluded"] = True
    assert "USER_EXCLUDED" in all_items(recommend(snapshot(), [row], now=NOW))[row["place_id"]]["reason_codes"]


def test_explanation_rejects_fake_place_numbers_slots_rank_and_references():
    rows = recommend(snapshot(), catalog(), now=NOW)["sections"]["local_discovery"]["items"]
    good = fallback(rows)
    payload = {"recommendations": good["recommendations"]}
    assert validate(rows, payload)
    for change in ("place", "order", "number", "slot", "score", "source", "unknown_field"):
        bad = deepcopy(payload)
        if change == "place": bad["recommendations"][0]["place_id"] = "invented"
        elif change == "order": bad["recommendations"].reverse()
        elif change == "number": bad["recommendations"][0]["reason_sentences"][0]["text"] = "현지인 99% 맛집"
        elif change == "slot": bad["recommendations"][0]["reason_sentences"][0]["text"] = "4인 예약 가능합니다."
        elif change == "score": bad["recommendations"][0]["score"] = 100
        elif change == "source": bad["recommendations"][0]["reason_sentences"][0]["source_ids"] = ["fake"]
        else: bad["recommendations"][0]["important_unknowns"] = []
        assert not validate(rows, bad)
        assert fallback(rows, bad) == good


def test_real_research_pack_remains_unranked_until_operator_policy_review():
    import json
    data = json.load(open("docs/service-v2/examples/real-discovery-candidates.json"))
    for citypack in data["packs"]:
        rows = []
        for n, row in enumerate(deepcopy(citypack["places"])):
            row.update(place_id=str(n), city=citypack["city"], identity_status="needs_confirmation", pack_status="needs_review", synthetic=False)
            rows.append(row)
        result = recommend(snapshot(citypack["city"]), rows, now=NOW)
        assert all(not section["items"] for section in result["sections"].values())
