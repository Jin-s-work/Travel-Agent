"""텍스트를 OpenAI 임베딩 벡터로 변환한다."""

from __future__ import annotations

import sys
from functools import lru_cache

from src.config import EMBEDDING_BATCH_SIZE, EMBEDDING_MODEL, OPENAI_API_KEY


@lru_cache(maxsize=1)
def _client():
    from openai import OpenAI

    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY가 설정되지 않았습니다. .env.example을 참고해 .env를 만드세요."
        )
    return OpenAI(api_key=OPENAI_API_KEY)


def embed_texts(
    texts: list[str],
    model: str = EMBEDDING_MODEL,
    batch_size: int = EMBEDDING_BATCH_SIZE,
    show_progress: bool = True,
) -> list[list[float]]:
    """텍스트 리스트를 배치로 나눠 임베딩한다. 입력 순서를 그대로 유지한다."""
    if not texts:
        return []
    if any(not text or not text.strip() for text in texts):
        raise ValueError("빈 텍스트는 임베딩할 수 없습니다.")

    vectors: list[list[float]] = []
    total_batches = (len(texts) + batch_size - 1) // batch_size

    for batch_index, start in enumerate(range(0, len(texts), batch_size), start=1):
        batch = texts[start : start + batch_size]
        response = _client().embeddings.create(model=model, input=batch)
        # API가 순서를 보장하지만, index로 다시 정렬해 확실히 맞춘다.
        vectors.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))

        if show_progress:
            done = min(start + batch_size, len(texts))
            print(
                f"  임베딩 {done}/{len(texts)} (배치 {batch_index}/{total_batches})",
                file=sys.stderr,
                flush=True,
            )

    return vectors


def embed_query(text: str, model: str = EMBEDDING_MODEL) -> list[float]:
    """검색 질의 한 건을 임베딩한다."""
    return embed_texts([text], model=model, show_progress=False)[0]
