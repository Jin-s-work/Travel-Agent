"""Pure recommendation engine. No network, database, clock reads except explicit default.

Component mappings are versioned hypotheses, not learned quality estimates. Missing
evidence remains None: no weight redistribution or invented neutral score.
"""
from __future__ import annotations
from src.destinations import CURRENCIES

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
from math import asin, cos, isfinite, radians, sin, sqrt
from typing import Any, Mapping
from zoneinfo import ZoneInfo


ENGINE_VERSION = "recommendation-engine-v1"
WEIGHTS = {
    "local_editorial_v1": {"local_evidence": .35, "preference": .25, "movement": .20, "visit_fit": .10, "price": .10},
    "iconic_v1": {"iconic_evidence": .30, "preference": .25, "movement": .20, "visit_fit": .15, "price": .10},
    "local_observed_v1": {"language": .25, "local_evidence": .20, "preference": .20, "movement": .15, "quality": .10, "visit_fit": .10},
}
# v1 remains reproducible; v2 uses raw geometry for boundaries and may score a
# verified walking route when the user explicitly prefers nearby places.
WEIGHTS.update({key.replace('_v1','_v2'):dict(value) for key,value in list(WEIGHTS.items())})
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


@dataclass(frozen=True)
class RankerConfig:
    version: str = "ranker-hypotheses-v1"
    chain_limit: int = 2
    neighborhood_limit: int = 3
    category_limit: int = 4
    distance_scale_m: int = 5000
    walking_scale_minutes: int = 60
    movement_version: str = "v1"

    def __post_init__(self):
        if any(type(x) is not int or x < 1 for x in (self.chain_limit, self.neighborhood_limit, self.category_limit, self.distance_scale_m, self.walking_scale_minutes)):
            raise ValueError("Ranker limits must be positive integers")
        if self.movement_version not in ("v1","v2"):raise ValueError("Unknown movement version")


def _stamp(value):
    if isinstance(value, datetime):
        output = value
    else:
        output = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if output.tzinfo is None:
        raise ValueError("Timezone-aware time required")
    return output.astimezone(timezone.utc)


def _date(value):
    return date.fromisoformat(str(value))


def _money(value):
    if not isinstance(value, (str, Decimal)):
        return None
    try:
        number = Decimal(value)
        return number if number.is_finite() and number >= 0 else None
    except InvalidOperation:
        return None


def _unit(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    number = float(value)
    return number if isfinite(number) and 0 <= number <= 1 else None


def _unique(values):
    return list(dict.fromkeys(x for x in values if x is not None))


class Facts:
    """Revalidate the DTO boundary; never rely on `usable` alone."""
    def __init__(self, candidate, visit, current):
        self.current, self.visit = current, visit
        self.sources = {}
        for source in candidate.get("sources", []):
            if (source.get("status") == "active" and source.get("read_confirmed") in (True, 1)
                    and source.get("display_permitted") in (True, 1) and source.get("id")):
                self.sources[source["id"]] = source
        self.rows = candidate.get("facts", [])

    def applicable(self, row):
        try:
            day = _date(self.visit["date"])
            if row.get("valid_for_date") and _date(row["valid_for_date"]) != day:
                return False
            if row.get("valid_from") and _date(row["valid_from"]) > day:
                return False
            if row.get("valid_until") and _date(row["valid_until"]) < day:
                return False
            return True
        except (ValueError, KeyError):
            return False

    def permitted(self, row):
        source = self.sources.get(row.get("source_id"))
        try:
            return bool(row.get("usable") is True and source
                        and row.get("freshness") != "expired"
                        and _stamp(row["checked_at"]) <= self.current < _stamp(row["expires_at"])
                        and _stamp(source["checked_at"]) >= _stamp(row["checked_at"])
                        and _stamp(source["checked_at"]) <= self.current
                        and source.get("policy_version") == row.get("policy_version")
                        and self.applicable(row))
        except (KeyError, TypeError, ValueError):
            return False

    def get(self, field):
        rows = [row for row in self.rows if row.get("field") == field and self.permitted(row)]
        if any(row.get("status") == "conflict" for row in rows):
            return None, [], "FACT_CONFLICT"
        verified = [row for row in rows if row.get("status") == "verified" and row.get("value") is not None]
        if not verified:
            return None, [], "FACT_UNCONFIRMED"
        if any(row["value"] != verified[0]["value"] for row in verified[1:]):
            return None, [], "FACT_CONFLICT"
        verified.sort(key=lambda row: (_stamp(row["checked_at"]), row["id"]), reverse=True)
        return deepcopy(verified[0]["value"]), verified, None

    @staticmethod
    def refs(rows):
        return sorted({row["source_id"] for row in rows if row.get("source_id")})


def _in_date_scope(rows, day):
    return any(row.get("valid_for_date") == day or
               (row.get("valid_from") and row.get("valid_until") and row["valid_from"] <= day <= row["valid_until"])
               for row in rows)


def _interval(raw):
    if isinstance(raw, list) and len(raw) == 2:
        start, end, offset = raw[0], raw[1], 0
        extra = {}
    elif isinstance(raw, dict):
        start, end, offset = raw.get("start"), raw.get("end"), raw.get("end_day_offset", 0)
        extra = raw
    else:
        raise ValueError("Invalid hours interval")
    a, b = time.fromisoformat(start), time.fromisoformat(end)
    if a.tzinfo or b.tzinfo or type(offset) is not int or offset not in (0, 1) or (not offset and b <= a):
        raise ValueError("Invalid hours interval")
    return a, b, offset, extra


def _hours(facts, visit):
    value, rows, reason = facts.get("opening_hours")
    refs = facts.refs(rows)
    if value is None:
        return "unknown", "HOURS_CONFLICT" if reason == "FACT_CONFLICT" else "HOURS_UNKNOWN", refs
    if not visit.get("local_time"):
        return "unknown", "VISIT_TIME_UNKNOWN", refs
    try:
        if value.get("timezone") != visit["timezone"]:
            return "unknown", "HOURS_TIMEZONE_MISMATCH", refs
        zone = ZoneInfo(visit["timezone"])
        day, clock = _date(visit["date"]), time.fromisoformat(visit["local_time"])
        if clock.tzinfo:
            return "unknown", "VISIT_TIME_INVALID", refs
        target = datetime.combine(day, clock)
        local = target.replace(tzinfo=zone)
        # Ambiguous/nonexistent civil time must be resolved explicitly.
        if local.utcoffset() != local.replace(fold=1).utcoffset() or local.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != target:
            return "unknown", "VISIT_TIME_AMBIGUOUS", refs
        future_unconfirmed = day != facts.current.astimezone(zone).date() and not _in_date_scope(rows, visit["date"])
        weekly = value.get("weekly")
        if not isinstance(weekly, dict):
            return "unknown", "HOURS_UNKNOWN", refs
        exceptions = value.get("exceptions", [])
        overrides = {}
        for exception in exceptions:
            if not isinstance(exception, dict) or not exception.get("date"):
                return "unknown", "HOURS_EXCEPTION_UNRESOLVED", refs
            if exception["date"] in (day.isoformat(), (day - timedelta(days=1)).isoformat()):
                if exception.get("closed") is True:
                    overrides[exception["date"]] = []
                elif isinstance(exception.get("intervals"), list):
                    overrides[exception["date"]] = exception["intervals"]
                else:
                    return "unknown", "HOURS_EXCEPTION_UNRESOLVED", refs
        # A verified exact-day closure takes precedence over yesterday's interval.
        if day.isoformat() in overrides and not overrides[day.isoformat()]:
            return "failed", "CLOSED_ON_VISIT", refs
        # A dated exception can confirm that day's schedule independently of the
        # general weekly template. No undated future hours become definitive.
        if future_unconfirmed and day.isoformat() not in overrides:
            return "unknown", "HOURS_DATE_UNCONFIRMED", refs
        known_today = False
        for offset in (0, -1):
            base = day + timedelta(days=offset)
            intervals = overrides.get(base.isoformat(), weekly.get(DAYS[base.weekday()], weekly.get(str(base.weekday()))))
            if offset == 0:
                known_today = intervals is not None
            if intervals is None:
                continue
            for raw in intervals:
                start, end, next_day, extra = _interval(raw)
                begin, finish = datetime.combine(base, start), datetime.combine(base + timedelta(days=next_day), end)
                if begin <= target < finish:
                    for name in ("last_order", "last_entry"):
                        if extra.get(name):
                            cutoff = datetime.combine(base, time.fromisoformat(extra[name]))
                            if cutoff < begin:
                                cutoff += timedelta(days=1)
                            if target >= cutoff:
                                return "failed", "AFTER_" + name.upper(), refs
                    return "confirmed", "HOURS_MATCH", refs
        return ("failed", "OUTSIDE_OPENING_HOURS", refs) if known_today else ("unknown", "HOURS_WEEKDAY_UNKNOWN", refs)
    except (KeyError, TypeError, ValueError):
        return "unknown", "HOURS_INVALID", refs


def _special_hours(facts, visit):
    """Separately stored closure/break/cutoff facts never disappear into hours."""
    checks = []
    day = _date(visit["date"])
    local_time = time.fromisoformat(visit["local_time"]) if visit.get("local_time") else None
    target = datetime.combine(day, local_time) if local_time else None
    closure, rows, reason = facts.get("exceptional_closures")
    if closure is not None:
        refs = facts.refs(rows)
        if isinstance(closure, dict):
            dates = closure.get("dates", [])
            ranges = closure.get("ranges", [])
        elif isinstance(closure, list):
            dates, ranges = closure, []
        else:
            dates, ranges = None, None
        try:
            if not isinstance(dates, list) or not isinstance(ranges, list):
                raise ValueError("Unparsed closure")
            closed = day in {_date(value) for value in dates}
            closed = closed or any(_date(value["start_date"]) <= day <= _date(value["end_date"]) for value in ranges)
            checks.append(("exceptional_closures", "failed" if closed else "confirmed", "CLOSED_ON_VISIT" if closed else "CLOSURE_DATE_CLEAR", refs))
        except (ValueError, KeyError, TypeError):
            checks.append(("exceptional_closures", "unknown", "CLOSURE_RULE_UNKNOWN", refs))
    elif reason == "FACT_CONFLICT":
        checks.append(("exceptional_closures", "unknown", "CLOSURE_RULE_CONFLICT", []))
    for field in ("break_times", "last_order", "last_entry"):
        value, rows, reason = facts.get(field)
        if value is None:
            if reason == "FACT_CONFLICT":
                checks.append((field, "unknown", field.upper()+"_CONFLICT", []))
            continue
        refs = facts.refs(rows)
        if not target:
            checks.append((field, "unknown", "VISIT_TIME_UNKNOWN", refs)); continue
        if day != facts.current.astimezone(ZoneInfo(visit["timezone"])).date() and not _in_date_scope(rows, visit["date"]):
            checks.append((field, "unknown", field.upper()+"_DATE_UNCONFIRMED", refs)); continue
        try:
            if field == "break_times":
                weekly = value.get("weekly", value) if isinstance(value, dict) else None
                inside = False
                for day_offset in (0, -1):
                    base = day+timedelta(days=day_offset)
                    intervals = weekly.get(DAYS[base.weekday()], weekly.get(str(base.weekday()), [])) if weekly is not None else value if day_offset == 0 else []
                    if not isinstance(intervals, list):
                        raise ValueError("Unknown break rule")
                    for interval in intervals:
                        start, end, offset, _ = _interval(interval)
                        inside = inside or datetime.combine(base, start) <= target < datetime.combine(base+timedelta(days=offset), end)
                checks.append((field, "failed" if inside else "confirmed", "DURING_BREAK" if inside else "BREAK_TIME_CLEAR", refs))
            else:
                cut = value.get("time") if isinstance(value, dict) else value
                offset = value.get("day_offset", 0) if isinstance(value, dict) else 0
                if type(offset) is not int or offset not in (0, 1):
                    raise ValueError("Invalid cutoff offset")
                cutoff = datetime.combine(day+timedelta(days=offset), time.fromisoformat(cut))
                missed = target >= cutoff
                checks.append((field, "failed" if missed else "confirmed", "AFTER_"+field.upper() if missed else field.upper()+"_MATCH", refs))
        except (KeyError, TypeError, ValueError):
            checks.append((field, "unknown", field.upper()+"_UNKNOWN", refs))
    return checks


def _coordinates(latitude, longitude):
    return (type(latitude) in (int, float) and type(longitude) in (int, float)
            and isfinite(latitude) and isfinite(longitude) and -90 <= latitude <= 90 and -180 <= longitude <= 180)


def movement(candidate, conditions):
    origin = conditions.get("origin") or {}
    if not (_coordinates(candidate.get("latitude"), candidate.get("longitude"))
            and _coordinates(origin.get("latitude"), origin.get("longitude"))):
        return {"kind": "unknown", "method": None, "distance_m": None, "provider": None, "duration_minutes": None}
    a, b, c, d = map(radians, (origin["latitude"], origin["longitude"], candidate["latitude"], candidate["longitude"]))
    haversine = sin((c - a) / 2) ** 2 + cos(a) * cos(c) * sin((d - b) / 2) ** 2
    meters = 2 * 6371008.8 * asin(sqrt(min(1, max(0, haversine))))
    return {"kind": "estimate", "method": "haversine_straight_line", "distance_m": round(meters, 1), "provider": None, "duration_minutes": None}



def straight_line_distance(origin, destination):
    """Unrounded great-circle meters. No implied path or walking speed."""
    from src.location.geometry import coordinate_pair,haversine_straight_line
    return haversine_straight_line(coordinate_pair(origin.get('latitude'),origin.get('longitude')),coordinate_pair(destination.get('latitude'),destination.get('longitude')))


def endpoint_version(candidate):
    # Public place coordinates must be bound to the exact candidate snapshot.
    import hashlib
    return hashlib.sha256(json.dumps({key:candidate.get(key) for key in ('place_id','latitude','longitude','sources')},sort_keys=True,separators=(',',':')).encode()).hexdigest()[:24]


def movement_v2(candidate, conditions, snapshot, current):
    origin=conditions.get('origin') or {}
    context=snapshot.get('origin_context') or {}
    selected=context.get('origin') or origin
    meters=straight_line_distance(origin,candidate)
    raw=deepcopy((snapshot.get('route_evidence') or {}).get(candidate['place_id']) or {})
    route={**raw,'status':'unknown','distance_m':None,'duration_seconds':None,'mode':conditions.get('transport','walking'),'reason_codes':raw.get('reason_codes') or ['ROUTE_NOT_CHECKED']}
    try:
        route_clock=_stamp(snapshot.get('route_evaluation_at') or current)
        distance,duration=raw.get('distance_m'),raw.get('duration_seconds')
        same=bool(raw.get('origin_identity',{}).get('place_id')) and raw['origin_identity']['place_id']==raw.get('destination_identity',{}).get('place_id') or bool(raw.get('origin_identity',{}).get('id')) and raw.get('origin_identity')==raw.get('destination_identity')
        valid_numbers=all(type(value) in (int,float) and isfinite(value) and value>=0 and (value>0 or same) for value in (distance,duration))
        origin_id=selected.get('accommodation_id') or origin.get('place_id') or 'manual-origin'
        origin_version=str(context.get('origin_version') or snapshot.get('origin_version') or 'manual-v1')
        identities=(raw.get('origin_identity',{}).get('id')==origin_id and str(raw.get('origin_identity',{}).get('version'))==origin_version and raw.get('destination_identity',{}).get('id')==candidate['place_id'] and str(raw.get('destination_identity',{}).get('version'))==endpoint_version(candidate))
        accessible=not (conditions.get('required') or {}).get('accessibility') or raw.get('accessibility_status')=='satisfied'
        permission=raw.get('usage_permission')
        permitted=permission is True or isinstance(permission,dict) and permission.get('display') is True and permission.get('durable_storage') is True
        if raw.get('status')=='ok' and raw.get('mode')==conditions.get('transport','walking') and valid_numbers and identities and permitted and _stamp(raw['checked_at'])<=route_clock<_stamp(raw['expires_at']) and accessible:
            route={**raw,'status':'ok','reason_codes':[]}
        elif raw.get('status')=='ok':
            route['reason_codes']=['ROUTE_CONTEXT_OR_FRESHNESS_UNCONFIRMED'] if accessible else ['ROUTE_ACCESSIBILITY_UNKNOWN']
    except (KeyError,TypeError,ValueError):
        route['reason_codes']=['ROUTE_DATA_INVALID']
    return {'kind':'provider' if route['status']=='ok' else 'estimate' if meters is not None else 'unknown',
        'method':'provider_route' if route['status']=='ok' else 'haversine_straight_line' if meters is not None else None,
        'distance_m':route['distance_m'] if route['status']=='ok' else meters,'straight_line_m':meters,
        'duration_minutes':route['duration_seconds']/60 if route['status']=='ok' else None,
        'provider':route.get('provider') if route['status']=='ok' else None,
        'origin':{key:selected.get(key) for key in ('label','accommodation_id','version')},
        'origin_version':context.get('origin_version'), 'route':route}


def route_candidates(snapshot,candidates,now,limit):
    """Deterministic cheap prefilter, before any external matrix request."""
    eligible=[]
    config=RankerConfig(movement_version='v2',version='movement-v2')
    for candidate in candidates:
        kinds=set(candidate.get('recommendation_types',[]))&set(snapshot['conditions']['recommendation_types'])
        if not kinds:continue
        output=_candidate(snapshot,candidate,sorted(kinds)[0],_stamp(now),config)
        if any(check['state']=='failed' for check in output['visit_fit']['checks']):continue
        distance=straight_line_distance(snapshot['conditions'].get('origin') or {},candidate)
        if distance is not None:eligible.append((distance,candidate['place_id'],candidate))
    eligible.sort(key=lambda item:(item[0],item[1]))
    return [row[2] for row in eligible[:limit]]

def _price(facts, conditions):
    price, rows, reason = facts.get("price")
    budget = conditions.get("budget")
    refs = facts.refs(rows)
    if not isinstance(price, dict) or price.get("currency") not in CURRENCIES or price.get("basis") not in {"per_person", "group"} or price.get("period") not in {"meal", "day", "visit"}:
        return None, "unknown", "PRICE_UNKNOWN", refs, None
    lower, upper = _money(price.get("amount_min")), _money(price.get("amount_max"))
    if lower is None and upper is None or lower is not None and upper is not None and lower > upper:
        return None, "unknown", "PRICE_UNKNOWN", refs, None
    if not budget or all(budget.get(k) is None for k in ("amount_min", "amount_max")):
        return price, "unknown", "BUDGET_UNKNOWN", refs, None
    if budget.get("currency") != price["currency"] or budget.get("period") != price["period"]:
        return price, "unknown", "PRICE_UNIT_MISMATCH", refs, None
    if budget.get("basis") != price["basis"]:
        if conditions["party"].get("children"):
            return price, "unknown", "CHILD_PRICE_UNKNOWN", refs, None
        factor = Decimal(conditions["party"]["adults"])
        if price["basis"] == "group" and budget.get("basis") == "per_person":
            factor = 1 / factor
        elif not (price["basis"] == "per_person" and budget.get("basis") == "group"):
            return price, "unknown", "PRICE_UNIT_MISMATCH", refs, None
        lower, upper = (x * factor if x is not None else None for x in (lower, upper))
    minimum, maximum = _money(budget.get("amount_min")), _money(budget.get("amount_max"))
    if (maximum is not None and lower is not None and lower > maximum) or (minimum is not None and upper is not None and upper < minimum):
        return price, "failed", "OUTSIDE_BUDGET", refs, 0.0
    if (maximum is not None and (upper is None or upper > maximum)) or (minimum is not None and (lower is None or lower < minimum)):
        return price, "unknown", "PRICE_RANGE_UNCONFIRMED", refs, None
    # Price fit is a binary supported budget fit, not a preference for expensive venues.
    return price, "confirmed", "BUDGET_MATCH", refs, 1.0


def _review(candidate, current):
    review = candidate.get("review_evidence") or {}
    evaluation = review.get("evaluation") or {}
    try:
        valid = (review.get("state") == "available" and _stamp(review["checked_at"]) <= current < _stamp(review["expires_at"])
                 and review.get("scope_mode") == "observed_window"
                 and review.get("population_inference_allowed") is False and review.get("residency_inference_allowed") is False)
    except (KeyError, ValueError, TypeError):
        valid = False
    if not valid:
        safe = {**deepcopy(review), "state": "unavailable", "counts": None, "metrics": None, "aggregate_id": None,
                "evaluation": {"decision": "unsupported", "strict_pass": False, "reason_codes": ["REVIEW_UNAVAILABLE"]}}
        return safe, False, ["REVIEW_UNAVAILABLE"]
    if evaluation.get("decision") != "pass" or evaluation.get("strict_pass") is not True:
        safe = deepcopy(review)
        if evaluation.get("decision") != "fail":
            safe["metrics"] = None
        return safe, False, evaluation.get("reason_codes") or ["REVIEW_NOT_QUALIFIED"]
    return review, True, []


def _rating(facts, settings, review=None):
    rating, rows, _ = facts.get("rating")
    refs = facts.refs(rows)
    if not isinstance(rating, dict) or rating.get("usage_permitted") is not True or not rating.get("platform") or rating.get("scale") != 5:
        return None, "unknown", "RATING_UNSUPPORTED", refs
    value, count = rating.get("rating"), rating.get("total_rating_count")
    if type(value) not in (int, float) or not isfinite(value) or not 0 <= value <= 5 or type(count) is not int or count < 0:
        return None, "unknown", "RATING_UNSUPPORTED", refs
    platform = (review or {}).get("platform")
    if platform and rating["platform"] != platform:
        return None, "unknown", "RATING_PLATFORM_MISMATCH", refs
    if value < settings.get("min_rating", 4.2) or count < settings.get("min_count", 200):
        return rating, "failed", "RATING_BELOW_THRESHOLD", refs
    return rating, "confirmed", "RATING_MATCH", refs


def _evidence_component(facts, field, synthetic):
    value, rows, reason = facts.get(field)
    refs = facts.refs(rows)
    if not isinstance(value, dict):
        return None, refs, [reason or "EVIDENCE_UNKNOWN"]
    actual = {s["source_group"]: s for s in facts.sources.values()}
    claimed = set(value.get("source_groups", []))
    confirmed = [actual[group] for group in claimed if group in actual]
    if value.get("evidence_type") == "direct_confirmation" or value.get("direct_confirmation") is True:
        return 1.0, refs, []
    if field == "local_evidence":
        regional = {s["source_group"] for s in confirmed if s.get("source_type") == "editorial" or synthetic and s.get("source_type") == "synthetic"}
        if regional:
            return min(1.0, len(regional) * .5), refs + [s["id"] for s in confirmed if s["source_group"] in regional], []
        return None, refs, ["INDEPENDENT_LOCAL_EVIDENCE_UNKNOWN"]
    if value.get("evidence_type") == "heritage":
        return .9, refs, []
    if value.get("evidence_type") == "official_landmark" or value.get("level") in {"city", "national", "world"}:
        return {"city": .6, "national": .8, "world": .9}.get(value.get("level"), .6), refs, []
    return None, refs, ["ICONIC_EVIDENCE_UNKNOWN"]


def _candidate(snapshot, candidate, kind, current, config):
    conditions = snapshot["conditions"]
    visit, party = conditions["visit"], conditions["party"]
    facts = Facts(candidate, visit, current)
    checks, reasons, used = [], [], set()

    def check(field, state, code, refs=()):
        refs = sorted(set(refs)); used.update(refs)
        checks.append({"field": field, "state": state, "reason_code": code, "source_ids": refs})
        if state != "confirmed":
            reasons.append(code)

    for valid, code in ((candidate.get("city") == conditions["city"], "CITY_MISMATCH"),
                        (candidate.get("category") in conditions["categories"], "CATEGORY_MISMATCH"),
                        (not candidate.get("excluded"), "USER_EXCLUDED"),
                        (candidate.get("identity_status") == "verified", "PLACE_IDENTITY_UNVERIFIED"),
                        (candidate.get("pack_status") == "approved", "PACK_UNAPPROVED")):
        if not valid:
            check("identity", "failed", code)
    if not facts.sources:
        check("source", "unknown", "SOURCE_POLICY_UNAVAILABLE")
    closed, rows, closed_reason = facts.get("closed")
    if closed is True:
        check("closed", "failed", "PLACE_CLOSED", facts.refs(rows))
    elif closed_reason == "FACT_CONFLICT":
        check("closed", "unknown", "BUSINESS_STATUS_CONFLICT")
    elif any(row.get("field") == "closed" and row.get("value") is not None
             and row.get("status") == "provisional" and facts.permitted(row) for row in facts.rows):
        check("closed", "unknown", "BUSINESS_STATUS_UNCONFIRMED")
    check("opening_hours", *_hours(facts, visit))
    for special_check in _special_hours(facts, visit):
        check(*special_check)
    total_party = party["adults"] + len(party.get("children", []))
    for field, compare in (("min_party", lambda n: total_party >= n), ("max_party", lambda n: total_party <= n)):
        value, rows, conflict = facts.get(field)
        if type(value) is not int or value < 1:
            check(field, "unknown", "PARTY_LIMIT_CONFLICT" if conflict == "FACT_CONFLICT" else field.upper() + "_UNKNOWN")
        else:
            check(field, "confirmed" if compare(value) else "failed", field.upper() + ("_MATCH" if compare(value) else "_MISMATCH"), facts.refs(rows))
    if party.get("children_status") == "unknown" and not party.get("children"):
        child_rule, child_rows, _ = facts.get("children_rule")
        if isinstance(child_rule, dict) and (child_rule.get("allowed") is False or (child_rule.get("minimum_age") or 0) > 0):
            check("children", "unknown", "CHILD_PARTY_UNKNOWN", facts.refs(child_rows))
    if party.get("children"):
        rule, rows, _ = facts.get("children_rule")
        refs = facts.refs(rows)
        if not isinstance(rule, dict) or type(rule.get("allowed")) is not bool:
            check("children", "unknown", "CHILDREN_RULE_UNKNOWN", refs)
        elif rule["allowed"] is False:
            check("children", "failed", "CHILDREN_NOT_ALLOWED", refs)
        elif any(child.get("age") is None for child in party["children"]):
            check("children", "unknown", "CHILD_AGE_UNKNOWN", refs)
        elif type(rule.get("minimum_age")) is not int:
            check("children", "unknown", "CHILD_AGE_RULE_UNKNOWN", refs)
        else:
            allowed = all(child["age"] >= rule["minimum_age"] for child in party["children"])
            check("children", "confirmed" if allowed else "failed", "CHILDREN_MATCH" if allowed else "CHILD_AGE_MISMATCH", refs)
    for field in ("dietary", "accessibility"):
        requested = (conditions.get("required") or {}).get(field, [])
        if requested:
            rules, rows, _ = facts.get(field)
            for name in requested:
                value = rules.get(name) if isinstance(rules, dict) else None
                state = "confirmed" if value is True else "failed" if value is False else "unknown"
                check(field + ":" + name, state, field.upper() + ("_MATCH" if state == "confirmed" else "_MISMATCH" if state == "failed" else "_UNKNOWN"), facts.refs(rows))
    travel = movement_v2(candidate, conditions, snapshot, current) if config.movement_version=="v2" else movement(candidate, conditions)
    straight=travel.get("straight_line_m",travel["distance_m"])
    if conditions.get("radius_m"):
        state = "unknown" if straight is None else "confirmed" if straight <= conditions["radius_m"] else "failed"
        check("radius", state, "RADIUS_UNKNOWN" if state == "unknown" else "RADIUS_MATCH" if state == "confirmed" else "OUTSIDE_RADIUS")
    distance_filter=conditions.get('distance_filter')
    if distance_filter:
        walking=distance_filter['kind']=='walking'
        actual=travel.get('duration_minutes') if walking and (travel.get('route') or {}).get('mode')=='walking' else None if walking else straight_line_distance(conditions.get('origin') or {},candidate)
        maximum=distance_filter['max_duration_minutes'] if walking else distance_filter['max_distance_m']
        state='unknown' if actual is None else 'confirmed' if actual<=maximum else 'failed'
        check('walking_duration' if walking else 'straight_line_distance',state,('WALKING_TIME_' if walking else 'STRAIGHT_LINE_')+('UNKNOWN' if state=='unknown' else 'MATCH' if state=='confirmed' else 'LIMIT_EXCEEDED'))
    price, price_state, price_reason, price_refs, price_score = _price(facts, conditions)
    if conditions.get("budget") and any(conditions["budget"].get(k) is not None for k in ("amount_min", "amount_max")):
        check("budget", price_state, price_reason, price_refs)
    review, qualified, review_reasons = _review(candidate, current)
    language_filter = snapshot.get("review_language_filter") or {}
    language_applies = kind in language_filter.get("apply_to", ["local_discovery"])
    strict = language_applies and language_filter.get("required") is True
    optional_observed = language_applies and language_filter.get("apply_only_if_qualified") is True and qualified
    model = "iconic_v1" if kind == "landmark" else "local_observed_v1" if strict or optional_observed else "local_editorial_v1"
    if config.movement_version=="v2":model=model.replace("_v1","_v2")
    if strict and not qualified:
        failed = (review.get("evaluation") or {}).get("decision") == "fail" and review.get("state") == "available"
        check("review_language", "failed" if failed else "unknown", "REVIEW_LANGUAGE_FAILED" if failed else "REVIEW_REQUIRED_UNSUPPORTED")
        reasons.extend(review_reasons)
    elif strict:
        check("review_language", "confirmed", "REVIEW_OBSERVED_MATCH")
    settings = snapshot.get("rating_filter") or {}
    rating_applies = settings.get("enabled") is True and kind in settings.get("apply_to", ["local_discovery"])
    rating_limits = settings if rating_applies else {"min_rating": 0, "min_count": 0}
    rating, rating_state, rating_reason, rating_refs = _rating(facts, rating_limits, review)
    if rating_applies:
        check("rating", rating_state, rating_reason, rating_refs)
    components = {}

    def component(name, value, refs=(), codes=()):
        refs = sorted(set(refs)); used.update(refs)
        components[name] = {"value": round(value, 6) if value is not None else None,
                            "weight": WEIGHTS[model][name], "source_ids": refs, "reason_codes": list(codes)}

    for field in ("local_evidence", "iconic_evidence"):
        if field in WEIGHTS[model]:
            component(field, *_evidence_component(facts, field, candidate.get("synthetic") is True))
    preferences = (conditions.get("preferred") or {}).get("tags", [])
    if preferences:
        requested = set(preferences)
        component("preference", len(requested.intersection(candidate.get("tags", []))) / len(requested))
    else:
        component("preference", None, codes=["PREFERENCE_UNKNOWN"])
    distance = straight
    use_walking=config.movement_version=='v2' and conditions.get('prefer_nearby') and (travel.get('route') or {}).get('status')=='ok' and (travel.get('route') or {}).get('mode')=='walking'
    component('movement',max(0.0,1-travel['duration_minutes']/config.walking_scale_minutes) if use_walking else max(0.0,1-distance/config.distance_scale_m) if distance is not None else None,
              codes=['VERIFIED_WALKING_ROUTE'] if use_walking else ['DISTANCE_ESTIMATE'] if distance is not None else ['ORIGIN_OR_COORDINATES_UNKNOWN'])
    confirmed = sum(item["state"] == "confirmed" for item in checks)
    component("visit_fit", confirmed / len(checks) if checks else None, [r for item in checks for r in item["source_ids"]])
    if "price" in WEIGHTS[model]:
        component("price", price_score, price_refs, [] if price_score is not None else [price_reason])
    if "quality" in WEIGHTS[model]:
        quality = rating["rating"] / 5 if rating and rating_state == "confirmed" and rating.get("comparison_cohort") else None
        component("quality", quality, rating_refs, [] if quality is not None else ["QUALITY_COMPARISON_UNSUPPORTED"])
    if "language" in WEIGHTS[model]:
        # Conservative lower bound within the qualified observed window only.
        signal = _unit((review.get("metrics") or {}).get("local_share_lower_bound")) if qualified else None
        component("language", signal, codes=[] if signal is not None else ["QUALIFIED_LANGUAGE_SIGNAL_UNKNOWN"])
    if candidate['place_id'] in snapshot.get('soft_avoid_place_ids',[]) and components.get('preference',{}).get('value') is not None:
        components['preference']['value'] *= .5
        components['preference']['reason_codes'].append('USER_SELECTED_SOFT_AVOID')
    missing = [name for name, item in components.items() if item["value"] is None]
    score = None if missing else float(sum(Decimal(str(item["value"])) * Decimal(str(item["weight"])) for item in components.values()))
    eligibility = "ineligible" if any(item["state"] == "failed" for item in checks) else "needs_confirmation" if any(item["state"] == "unknown" for item in checks) else "eligible"
    refs = [deepcopy(facts.sources[ident]) for ident in sorted(used) if ident in facts.sources]
    reason_codes = _unique(reasons + [code for item in components.values() for code in item["reason_codes"]])
    important = _unique([item["reason_code"] for item in checks if item["state"] == "unknown"])
    important.append("LIVE_AVAILABILITY_NOT_CONFIRMED")
    output = {key: deepcopy(candidate.get(key)) for key in ("place_id", "name", "native_name", "address", "city", "category", "synthetic", "canonical_url", "chain_id", "neighborhood")}
    output.update(recommendation_type=kind, eligibility=eligibility, reason_codes=reason_codes,
                  important_unknowns=important, ranker_version=model, config_version=config.version,
                  score=round(score, 6) if score is not None else None, score_components=components,
                  missing_components=missing, source_refs=refs, checked_at=min((s["checked_at"] for s in refs), default=None),
                  visit_fit={"checks": checks, "confirmed_count": confirmed, "required_count": len(checks)},
                  price_basis=price, movement=travel, review_evidence=deepcopy(review))
    output["facts"] = [{**deepcopy(row), "value": deepcopy(row.get("value")) if facts.permitted(row) else None,
                        "usable": facts.permitted(row)} for row in facts.rows]
    output["sources"] = refs
    from .explanations import render
    output["supported_reasons"] = render(output)
    output["reason_sentences"] = [{k: v for k, v in item.items() if k != "code"} for item in output["supported_reasons"]]
    return output


def _diversity(items, limit, config):
    kept, omitted = [], []
    chain, neighborhood, category = {}, {}, {}
    for item in sorted(items, key=lambda value: (-value["score"], value["place_id"])):
        checks = ((chain, item.get("chain_id"), config.chain_limit, "CHAIN_LIMIT"),
                  (neighborhood, item.get("neighborhood"), config.neighborhood_limit, "NEIGHBORHOOD_LIMIT"),
                  (category, item.get("category"), config.category_limit, "CATEGORY_LIMIT"))
        limited = next((reason for counts, key, cap, reason in checks if key and counts.get(key, 0) >= cap), None)
        if len(kept) >= limit or limited:
            item["reason_codes"].append(limited or "RESULT_LIMIT")
            item["selection_status"] = "diversity_deferred" if limited else "limit_deferred"
            omitted.append(item)
            continue
        item["selection_status"] = "selected"
        kept.append(item)
        for counts, key, _, _ in checks:
            if key:
                counts[key] = counts.get(key, 0) + 1
    return kept, omitted


def recommend(snapshot: Mapping[str, Any], candidates: list[dict], now=None, config=None):
    """Return reproducible ranked/confirmation/reference/excluded groups.

    `now` must be timezone aware; callers freeze it in the durable run snapshot.
    Untrusted or malformed candidate facts fail closed rather than becoming fit.
    """
    current = _stamp(now or datetime.now(timezone.utc))
    config = config or RankerConfig(movement_version=snapshot.get("movement_version","v1"),version="ranker-movement-v2" if snapshot.get("movement_version")=="v2" else "ranker-hypotheses-v1")
    if isinstance(config, dict):
        config = RankerConfig(**config)
    from src.discovery.models import Conditions
    snapshot = deepcopy(dict(snapshot))
    snapshot["conditions"] = Conditions.model_validate(snapshot["conditions"]).model_dump(mode="json")
    limit = snapshot.get("limit", 6)
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("Recommendation limit must be 1 to 20")
    sections = {kind: {key: [] for key in ("items", "needs_confirmation", "insufficient_data", "excluded")} for kind in ("local_discovery", "landmark")}
    # Duplicate internal IDs are the same branch. Do not merge chain branches.
    unique = {}
    for item in candidates:
        ident = item.get("place_id")
        if ident:
            prior = unique.get(ident)
            if prior is None:
                unique[ident] = deepcopy(item)
            elif prior != item:
                # Divergent rows for one internal branch ID need reconciliation;
                # ordering of provider responses cannot select favorable facts.
                selected = min((prior, item), key=lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False))
                unique[ident] = {**deepcopy(selected), "identity_status": "needs_confirmation"}
    for kind in snapshot["conditions"]["recommendation_types"]:
        for ident in sorted(unique):
            source = unique[ident]
            if kind not in source.get("recommendation_types", []):
                continue
            item = _candidate(snapshot, source, kind, current, config)
            target = "excluded" if item["eligibility"] == "ineligible" else "needs_confirmation" if item["eligibility"] == "needs_confirmation" else "insufficient_data" if item["score"] is None else "items"
            sections[kind][target].append(item)
        selected, deferred = _diversity(sections[kind]["items"], limit, config)
        sections[kind]["items"] = selected
        sections[kind]["excluded"].extend(deferred)
    reasons = []
    for kind in snapshot["conditions"]["recommendation_types"]:
        if len(sections[kind]["items"]) < limit:
            reasons.append("INSUFFICIENT_QUALIFIED_" + kind.upper())
    counters = {kind: {key: len(value) for key, value in groups.items()} for kind, groups in sections.items()}
    return {"sections": sections, "counters": counters, "reason_codes": reasons,
            "engine_version": ENGINE_VERSION, "config_version": config.version, "config": asdict(config),
            "computed_at": current.isoformat(), "requested_constraints": deepcopy(snapshot),
            "applied_constraints": {"conditions": deepcopy(snapshot["conditions"]), "rating_filter": deepcopy(snapshot.get("rating_filter", {})), "review_language_filter": deepcopy(snapshot.get("review_language_filter", {}))},
            "distance_exclusions": {code:sum(code in item["reason_codes"] for groups in sections.values() for name in ("excluded","needs_confirmation") for item in groups[name]) for code in ("WALKING_TIME_UNKNOWN","WALKING_TIME_LIMIT_EXCEEDED","STRAIGHT_LINE_UNKNOWN","STRAIGHT_LINE_LIMIT_EXCEEDED","OUTSIDE_RADIUS","RADIUS_UNKNOWN")},
            "unsupported_constraints": sorted({code for groups in sections.values() for key in ("needs_confirmation", "insufficient_data") for item in groups[key] for code in item["reason_codes"]})}
