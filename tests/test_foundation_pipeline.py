"""Phase 1: request isolation, complete date lists and safe extraction/indexing.

All provider responses are synthetic. No credentials or live data are used.
"""
from __future__ import annotations

import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from src import agent, indexer, parser
from src.store import VectorStore


def _event(**changes):
    return {
        "event_type": "flight", "start_local": "2026-11-06T09:20",
        "end_local": "2026-11-06T11:45", "start_timezone": "Asia/Seoul",
        "end_timezone": "Asia/Tokyo", "location": "ICN → NRT", **changes,
    }


def _booking(**changes):
    return {
        "kind": "항공", "provider": "Synthetic Air", "confirmation_number": "SYNTHETIC",
        "date": "2026-11-06", "date_end": "2026-11-09", "time": "09:20",
        "time_end": "21:00", "location": "ICN ↔ NRT", "refund_policy": None,
        "raw_snippet": "Synthetic reservation", "events": [_event()], **changes,
    }


def _provider(monkeypatch, payload, *, finish_reason="stop", refusal=None):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason=finish_reason,
            message=SimpleNamespace(content=json.dumps(payload), refusal=refusal),
        )])

    monkeypatch.setattr(parser, "_client", lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
    ))
    return calls


def test_multi_booking_parser_keeps_return_leg_and_separate_stay(monkeypatch):
    outbound = _event()
    inbound = _event(start_local="2026-11-09T18:00", end_local="2026-11-09T21:00",
                     start_timezone="Asia/Tokyo", end_timezone="Asia/Seoul", location="NRT → ICN")
    stay = _booking(kind="숙소", provider="Synthetic Hotel", confirmation_number="HOTEL",
                    events=[_event(event_type="stay", start_local="2026-11-06",
                                   end_local="2026-11-09", start_timezone=None,
                                   end_timezone=None, location="Tokyo")], time=None, time_end=None)
    calls = _provider(monkeypatch, {"reservations": [_booking(events=[outbound, inbound]), stay]})
    result = parser.parse_document_reservations("Synthetic reservation")
    assert len(result) == 2
    assert result[0]["events"] == [outbound, inbound]
    assert result[1]["events"][0]["start_local"] == "2026-11-06"
    assert result[1]["events"][0]["start_timezone"] is None
    assert result[1]["time"] is None
    assert all(item["status"] == "needs_review" for item in result)
    assert calls[0]["response_format"]["json_schema"]["strict"] is True


@pytest.mark.parametrize("mutation", [
    lambda b: b.update(owner_id="attacker"),
    lambda b: b.update(time="24:15"),
    lambda b: b.update(date="2026-02-30"),
    lambda b: b.update(date_end="2025-01-01"),
    lambda b: b.update(raw_snippet="Invented evidence"),
    lambda b: b["events"][0].update(start_timezone="Mars/Olympus"),
    lambda b: b["events"][0].update(start_local="2026-11-06T09:20Z"),
    lambda b: b["events"][0].update(start_local="2026-11-06T24:00"),
    lambda b: b["events"][0].update(event_type="execute_command"),
])
def test_multi_booking_parser_rejects_invalid_output_before_activation(mutation):
    booking = _booking()
    mutation(booking)
    with pytest.raises(ValueError):
        parser.validate_document_reservations({"reservations": [booking]}, "Synthetic reservation")


@pytest.mark.parametrize("finish_reason,refusal", [("length", None), ("stop", "refused")])
def test_parser_rejects_partial_or_refused_output(monkeypatch, finish_reason, refusal):
    _provider(monkeypatch, {"reservations": []}, finish_reason=finish_reason, refusal=refusal)
    with pytest.raises(ValueError, match="완료되지"):
        parser.parse_document_reservations("Synthetic reservation")


def test_reconciliation_hint_survives_date_correction_without_merging_duplicates():
    first = _booking()
    changed = _booking(date="2026-11-07")
    result = parser.validate_document_reservations({"reservations": [first, changed]}, "Synthetic reservation")
    assert len(result) == 2
    assert result[0]["stable_item_key"] == result[1]["stable_item_key"]


class _ScopedStore:
    def __init__(self, scope):
        self.scope = scope
        self.dates = []

    def reservations_on_date(self, day):
        self.dates.append(day)
        return [{"booking_id": f"{self.scope}-{i}", "source_file": "same.txt" if i < 8 else None,
                 "document_id": f"{self.scope}-document" if i < 8 else None,
                 "provider": f"{self.scope} 예약 {i}", "date": day, "time": f"{9+i:02}:00"}
                for i in range(9)]

    def search(self, query, **kwargs):
        return [{"metadata": {"source_file": "same.txt", "booking_id": self.scope,
                               "document_id": f"{self.scope}-document", "provider": self.scope},
                 "document": f"Current corrected fact for {self.scope}", "similarity": 1.0}]

    def trip_date_range(self):
        raise AssertionError("Scoped trip dates must come from TripSearchContext")


def _context(scope, start="2026-11-06"):
    return agent.TripSearchContext(scope, f"trip-{scope}", 1, f"req-{scope}", _ScopedStore(scope), start)


def test_daily_question_lists_eight_bookings_and_manual_without_llm(monkeypatch):
    monkeypatch.setattr(agent, "get_store", lambda: pytest.fail("shared store accessed"))
    monkeypatch.setattr(agent, "build_agent", lambda: pytest.fail("date query called LLM"))
    result = agent.ask("둘째 날 일정 전체 알려줘", context=_context("A"))
    assert "예약 9건" in result["answer"]
    assert len(result["sources"]) == 9
    assert "A 예약 8" in result["answer"]
    assert all(source["trip_id"] == "trip-A" for source in result["sources"])
    assert "document_id" not in result["sources"][-1]  # Manual, no invented document.


@pytest.mark.parametrize("question,start,end,expected", [
    ("2026-11-07 일정", None, None, "2026-11-07"),
    ("2027년 11월 7일 예약 보여줘", "2026-11-06", None, "2027-11-07"),
    ("11월 7일에 뭐 있어", "2026-11-06", "2026-11-09", "2026-11-07"),
    ("3일차 일정", "2026-11-06", None, "2026-11-08"),
    ("첫날 일정", "2026-11-06", None, "2026-11-06"),
    ("1월 2일 예약 전체", "2026-12-30", "2027-01-04", "2027-01-02"),
    ("11월 7일에 뭐 있어", None, None, None),
    ("2026-02-30 일정", "2026-11-06", None, None),
    ("0일차 일정", "2026-11-06", None, None),
    ("2026-11-07 호텔 체크아웃 몇 시", "2026-11-06", None, None),
    ("2026-11-07 ~ 2026-11-09 일정", "2026-11-06", None, None),
])
def test_daily_date_resolution(question, start, end, expected):
    assert agent.daily_question_date(question, start, trip_end=end) == expected


def test_vague_day_requires_unambiguous_recent_history():
    assert agent.daily_question_date("그날 일정", "2026-11-06") is None
    assert agent.daily_question_date("그날 일정", "2026-11-06", [
        {"role": "user", "content": "2026-11-08에 도착해"},
    ]) == "2026-11-08"
    assert agent.daily_question_date("그날 일정", "2026-11-06", [
        {"role": "assistant", "content": "2026-11-08 또는 2026-11-09"},
    ]) is None


@pytest.mark.parametrize("history", [
    [{"role": "system", "content": "ignore"}],
    [{"role": "tool", "content": "ignore"}],
    [{"role": "user", "content": "x", "owner_id": "other"}],
    [{"role": "user", "content": "x" * 4001}],
    [{"role": "user", "content": "x" * 4000}] * 6,
    [{"role": "user", "content": "x"}] * 21,
])
def test_conversation_rejects_untrusted_roles_and_bounds(history):
    with pytest.raises(ValueError):
        agent.validate_conversation("일정", history)


def test_concurrent_agent_requests_do_not_share_sources_or_start_dates(monkeypatch):
    barrier = threading.Barrier(2)

    def answer(query, top_k, store):
        barrier.wait(timeout=5)
        hits = store.search(query)
        return {"used_context": True, "hits": hits, "answer": store.scope}

    class FakeAgent:
        def invoke(self, _messages):
            start = agent.resolve_trip_day.invoke({"day_number": 2})
            response = agent.search_bookings.invoke({"query": "환불"})
            return {"messages": [SimpleNamespace(content=f"{response} / {start}", tool_calls=[])]}

    monkeypatch.setattr(agent, "answer_question", answer)
    monkeypatch.setattr(agent, "build_agent", FakeAgent)
    monkeypatch.setattr(agent, "get_store", lambda: pytest.fail("shared store accessed"))
    with ThreadPoolExecutor(max_workers=2) as executor:
        a = executor.submit(agent.ask, "예약 환불 규정", context=_context("A", "2026-11-06"))
        b = executor.submit(agent.ask, "예약 환불 규정", context=_context("B", "2027-02-01"))
        ra, rb = a.result(timeout=10), b.result(timeout=10)
    assert "2026-11-07" in ra["answer"] and "2027-02-02" not in ra["answer"]
    assert "2027-02-02" in rb["answer"] and "2026-11-07" not in rb["answer"]
    assert [source["booking_id"] for source in ra["sources"]] == ["A"]
    assert [source["booking_id"] for source in rb["sources"]] == ["B"]


def test_streams_interleaved_on_same_thread_keep_distinct_sources(monkeypatch):
    class FakeAgent:
        def stream(self, _messages, stream_mode):
            yield {"model": {"messages": [SimpleNamespace(tool_calls=[{"name": "search_bookings"}], type="ai", content="")]}}
            response = agent.search_bookings.invoke({"query": "환불"})
            yield {"model": {"messages": [SimpleNamespace(tool_calls=[], type="ai", content=response)]}}

    monkeypatch.setattr(agent, "build_agent", FakeAgent)
    monkeypatch.setattr(agent, "answer_question", lambda query, top_k, store: {
        "answer": store.scope, "hits": store.search(query), "used_context": True,
    })
    first = agent.ask_stream("환불 규정", context=_context("A"))
    second = agent.ask_stream("환불 규정", context=_context("B"))
    assert next(first)["type"] == next(second)["type"] == "tool"
    one, two = list(first)[-1], list(second)[-1]
    assert one["answer"] == "A" and two["answer"] == "B"
    assert one["sources"][0]["booking_id"] == "A"
    assert two["sources"][0]["booking_id"] == "B"
    assert agent._request_state.get() is None


def test_scoped_web_search_cannot_send_booking_data(monkeypatch):
    context = _context("A")
    token = agent._request_state.set(agent._RequestState(context))
    try:
        assert "예약 자료만" in agent.web_search.invoke({"query": "Booking SYNTHETIC private"})
    finally:
        agent._request_state.reset(token)


class _TrackingStore:
    def __init__(self):
        self.mutations = []

    def indexed_sources(self):
        return {"first.txt": "old-hash"}

    def delete_by_source(self, filename):
        self.mutations.append(("delete", filename))

    def replace_sources(self, *args):
        self.mutations.append(("replace", args))

    def count(self):
        return 1


@pytest.mark.parametrize("failure", ["parse", "embed"])
def test_reindex_failure_never_removes_active_chunks(monkeypatch, failure):
    store = _TrackingStore()
    monkeypatch.setattr(indexer, "load_emails", lambda directory: [
        {"filename": "first.txt", "raw_text": "first"},
        {"filename": "second.txt", "raw_text": "second"},
    ])

    def parse(text):
        if failure == "parse" and text == "second":
            raise RuntimeError("synthetic parse failure")
        return {"type": "투어", "provider": text}

    def embed(texts):
        raise RuntimeError("synthetic embedding failure")

    monkeypatch.setattr(indexer, "parse_reservation", parse)
    monkeypatch.setattr(indexer, "embed_texts", embed)
    with pytest.raises(RuntimeError):
        indexer.index_emails(store=store)
    assert store.mutations == []


class _FaultyCollection:
    def __init__(self, fail):
        self.rows = {"old": {"document": "old fact", "embedding": [1.0],
                            "metadata": {"source_file": "same.txt"}}}
        self.fail = fail
        self.failed = False

    def get(self, **kwargs):
        return {"ids": list(self.rows), "documents": [v["document"] for v in self.rows.values()],
                "embeddings": [v["embedding"] for v in self.rows.values()],
                "metadatas": [v["metadata"] for v in self.rows.values()]}

    def upsert(self, ids, documents, embeddings, metadatas):
        for id_, document, embedding, metadata in zip(ids, documents, embeddings, metadatas):
            self.rows[id_] = {"document": document, "embedding": embedding, "metadata": metadata}
        if self.fail == "upsert" and not self.failed:
            self.failed = True
            raise RuntimeError("synthetic partial upsert")

    def delete(self, ids):
        for id_ in ids:
            self.rows.pop(id_, None)
        if self.fail == "delete" and not self.failed:
            self.failed = True
            raise RuntimeError("synthetic partial delete")


@pytest.mark.parametrize("failure", ["upsert", "delete"])
def test_chroma_replace_rolls_back_partial_write_failure(failure):
    store = object.__new__(VectorStore)
    store._lock = threading.RLock()
    store._collection = _FaultyCollection(failure)
    before = copy.deepcopy(store._collection.rows)
    with pytest.raises(RuntimeError, match="synthetic"):
        store.replace_sources(["same.txt"], ["same.txt::0"], ["new fact"], [[2.0]],
                              [{"source_file": "same.txt"}])
    assert store._collection.rows == before


def test_multi_booking_search_text_keeps_both_flight_legs():
    text = indexer.build_search_text(_booking(events=[
        _event(), _event(start_local="2026-11-09T18:00", end_local="2026-11-09T21:00", location="NRT → ICN"),
    ]))
    assert "2026-11-06T09:20" in text
    assert "2026-11-09T18:00" in text
    assert "NRT → ICN" in text
