"""검색 품질과 생성 품질을 분리해서 평가한다.

검색(R)과 생성(G)을 따로 재는 것이 핵심이다. 답이 틀렸을 때
"정답 메일을 못 찾은 것"과 "찾았는데 답을 틀린 것"은 고쳐야 할 곳이 다르다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from src.config import NO_INFO_MESSAGE, PROJECT_ROOT, TOP_K
from src.rag import answer_question
from src.store import VectorStore

EVAL_SET_PATH = PROJECT_ROOT / "tests" / "eval_set.json"

# 실패 원인 분류
# 검색 실패와 임계값 탈락은 반드시 구분해야 한다. 전자는 임베딩·청킹을,
# 후자는 임계값 설정을 고쳐야 하므로 처방이 완전히 다르다.
RETRIEVAL_FAILURE = "검색 실패(top-k 밖)"
THRESHOLD_FAILURE = "임계값 탈락(검색은 성공)"
TYPE_FILTER_FAILURE = "타입 필터 탈락(질문의 예약 종류를 잘못 감지)"
GENERATION_FAILURE = "생성 실패"
HALLUCINATION = "환각(거절 실패)"


def load_eval_set(path: str | Path = EVAL_SET_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as fp:
        return json.load(fp)["items"]


def evaluate(
    top_k: int = TOP_K,
    store: VectorStore | None = None,
    eval_set: list[dict] | None = None,
    verbose: bool = True,
) -> dict:
    """평가셋 전체를 돌려 검색·생성 지표를 계산한다."""
    store = store or VectorStore()
    items = eval_set if eval_set is not None else load_eval_set()

    rows = []
    for item in items:
        if verbose:
            print(f"  평가 중: {item['id']} {item['question'][:30]}", file=sys.stderr, flush=True)
        rows.append(_evaluate_one(item, store, top_k))

    return _summarize(rows, top_k)


def _evaluate_one(item: dict, store: VectorStore, top_k: int) -> dict:
    question = item["question"]

    # --- (a) 검색 품질 ---
    # 임계값 없는 순수 검색 순위로 recall을 잰다(표준 정의).
    raw_hits = store.search(question, top_k=top_k, min_similarity=None)
    raw_sources = [hit["metadata"].get("source_file") for hit in raw_hits]

    # 임계값만 적용한 결과. 타입 필터와 원인을 분리하기 위해 따로 잰다.
    threshold_hits = store.search(question, top_k=top_k)
    threshold_sources = [hit["metadata"].get("source_file") for hit in threshold_hits]

    # --- (b) 생성 품질 ---
    result = answer_question(question, top_k=top_k, store=store)
    answer = result["answer"]

    # 실제로 생성 단계에 전달된 근거. answer_question은 질문에서 예약 종류를
    # 감지하면 타입 필터를 추가로 걸므로, store.search 결과와 다를 수 있다.
    context_sources = [hit["metadata"].get("source_file") for hit in result["hits"]]

    expected = item.get("expected_source_file")
    recall_raw = expected in raw_sources if expected else None
    recall_kept = expected in context_sources if expected else None

    if item["should_refuse"]:
        passed = _is_refusal(answer)
        failure = None if passed else HALLUCINATION
        missing = []
    else:
        missing = [
            keyword
            for keyword in item["expected_answer_contains"]
            if keyword.lower() not in answer.lower()
        ]
        passed = not missing
        if passed:
            failure = None
        elif not recall_raw:
            # 애초에 top-k 안에 못 들어왔다 → 임베딩·청킹 문제.
            failure = RETRIEVAL_FAILURE
        elif expected not in threshold_sources:
            # top-k에는 있었는데 임계값이 잘랐다 → 임계값 설정 문제.
            failure = THRESHOLD_FAILURE
        elif not recall_kept:
            # 임계값은 통과했는데 생성 단계에 없다 → 타입 필터가 제거했다.
            failure = TYPE_FILTER_FAILURE
        else:
            # 근거는 받았는데 답이 틀렸다 → 프롬프트 문제.
            failure = GENERATION_FAILURE

    return {
        "id": item["id"],
        "question": question,
        "should_refuse": item["should_refuse"],
        "expected_source_file": expected,
        "recall_raw": recall_raw,
        "recall_kept": recall_kept,
        "retrieved": raw_sources,
        "context_sources": context_sources,
        "kept_count": len(result["hits"]),
        "answer": answer,
        "missing_keywords": missing,
        "passed": passed,
        "failure_type": failure,
        "top_similarity": raw_hits[0]["similarity"] if raw_hits else None,
    }


def _is_refusal(answer: str) -> bool:
    """모델이 실제로 거절했는지 판정한다."""
    normalized = answer.replace(" ", "")
    markers = [
        NO_INFO_MESSAGE.replace(" ", ""),
        "확인할수없",
        "확인되지않",
        "찾을수없",
        "정보가없",
        "포함되어있지않",
    ]
    return any(marker in normalized for marker in markers)


def _summarize(rows: list[dict], top_k: int) -> dict:
    answerable = [row for row in rows if not row["should_refuse"]]
    refusable = [row for row in rows if row["should_refuse"]]

    recall_hits = sum(1 for row in answerable if row["recall_raw"])
    recall_kept_hits = sum(1 for row in answerable if row["recall_kept"])
    answer_hits = sum(1 for row in answerable if row["passed"])
    refusal_hits = sum(1 for row in refusable if row["passed"])

    return {
        "top_k": top_k,
        "rows": rows,
        "counts": {
            "total": len(rows),
            "answerable": len(answerable),
            "refusable": len(refusable),
        },
        "metrics": {
            "recall_at_k": _ratio(recall_hits, len(answerable)),
            "recall_at_k_after_threshold": _ratio(recall_kept_hits, len(answerable)),
            "answer_accuracy": _ratio(answer_hits, len(answerable)),
            "hallucination_prevention": _ratio(refusal_hits, len(refusable)),
        },
        "raw": {
            "recall": (recall_hits, len(answerable)),
            "recall_kept": (recall_kept_hits, len(answerable)),
            "answer": (answer_hits, len(answerable)),
            "refusal": (refusal_hits, len(refusable)),
        },
    }


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def print_report(summary: dict) -> None:
    top_k = summary["top_k"]
    metrics = summary["metrics"]
    raw = summary["raw"]

    print("\n" + "=" * 78)
    print(f"평가 결과 (top_k={top_k}, 질문 {summary['counts']['total']}개)")
    print("=" * 78)
    print(f"{'지표':32} {'점수':>8}   {'비율':>10}")
    print("-" * 78)
    print(f"{'검색 recall@' + str(top_k) + ' (임계값 전)':32} "
          f"{metrics['recall_at_k']:>7.1%}   {raw['recall'][0]:>4}/{raw['recall'][1]}")
    print(f"{'검색 recall@' + str(top_k) + ' (임계값 후)':32} "
          f"{metrics['recall_at_k_after_threshold']:>7.1%}   {raw['recall_kept'][0]:>4}/{raw['recall_kept'][1]}")
    print(f"{'답변 정확도 (키워드 포함)':32} "
          f"{metrics['answer_accuracy']:>7.1%}   {raw['answer'][0]:>4}/{raw['answer'][1]}")
    print(f"{'환각 방지율 (거절해야 할 질문)':32} "
          f"{metrics['hallucination_prevention']:>7.1%}   {raw['refusal'][0]:>4}/{raw['refusal'][1]}")

    print("\n" + "-" * 78)
    print(f"{'ID':5} {'구분':6} {'검색':5} {'생성':5} {'유사도':>7}  질문")
    print("-" * 78)
    for row in summary["rows"]:
        kind = "거절" if row["should_refuse"] else "응답"
        retrieval = "-" if row["recall_raw"] is None else ("OK" if row["recall_raw"] else "MISS")
        generation = "OK" if row["passed"] else "FAIL"
        similarity = f"{row['top_similarity']:.3f}" if row["top_similarity"] is not None else "-"
        print(f"{row['id']:5} {kind:6} {retrieval:5} {generation:5} {similarity:>7}  {row['question'][:38]}")

    failures = [row for row in summary["rows"] if not row["passed"]]
    print("\n" + "=" * 78)
    if not failures:
        print("실패한 질문 없음")
        return

    print(f"실패한 질문 {len(failures)}개 — 원인별")
    print("=" * 78)
    for row in failures:
        print(f"\n[{row['id']}] {row['failure_type']}")
        print(f"  질문   : {row['question']}")
        if row["should_refuse"]:
            print("  기대   : 거절해야 함 (예약 메일에 없는 정보)")
        else:
            print(f"  정답 메일: {row['expected_source_file']}")
            print(f"  검색 결과: {row['retrieved']}")
            print(f"  누락 키워드: {row['missing_keywords']}")
        print(f"  실제 답변: {row['answer'][:200]}")


def save_report(summary: dict, directory: Path | None = None) -> Path:
    """평가 결과를 JSON으로 남긴다.

    실행할 때마다 시간이 오래 걸리므로, 수정 전후 비교를 위해 결과를
    파일로 보존한다. data/ 아래라 git에는 올라가지 않는다.
    """
    from datetime import datetime

    directory = directory or (PROJECT_ROOT / "data" / "eval_runs")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"eval_{datetime.now():%Y%m%d_%H%M%S}.json"
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(summary, fp, ensure_ascii=False, indent=2)
    return path


if __name__ == "__main__":
    args = sys.argv[1:]
    k = TOP_K
    if "--top-k" in args:
        k = int(args[args.index("--top-k") + 1])

    result = evaluate(top_k=k)
    print_report(result)

    saved = save_report(result)
    # 리포트를 저장소에 커밋할 수 있으므로 로컬 절대 경로는 찍지 않는다.
    print(f"\n결과 저장: {saved.relative_to(PROJECT_ROOT)}")
