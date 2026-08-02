"""Chroma 기반 벡터 저장/검색."""

from __future__ import annotations

from pathlib import Path

from src.config import (
    CHROMA_DIR,
    COLLECTION_NAME,
    DISTANCE_METRIC,
    MIN_SIMILARITY,
    RELATIVE_SIMILARITY_RATIO,
    TOP_K,
)
from src.embedder import embed_query


class VectorStore:
    """예약 메일 청크를 저장하고 의미 검색을 수행한다."""

    def __init__(
        self,
        persist_dir: str | Path = CHROMA_DIR,
        collection_name: str = COLLECTION_NAME,
    ):
        # chromadb import는 수 초에서 수십 초가 걸린다. 임계값 계산 같은 순수 함수만
        # 쓰는 쪽(단위 테스트 등)이 이 비용을 치르지 않도록 여기서 불러온다.
        import chromadb
        from chromadb.config import Settings

        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection_name

        self._client = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=Settings(anonymized_telemetry=False),
        )
        # embedding_function=None: 임베딩은 embedder.py에서 직접 만들어 넣는다.
        # 생략하면 Chroma가 기본 ONNX 모델을 내려받으려 한다.
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            configuration={"hnsw": {"space": DISTANCE_METRIC}},
            embedding_function=None,
        )

    def add(
        self,
        ids: list[str],
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict],
    ) -> int:
        """청크를 저장한다. 같은 id가 있으면 덮어쓴다."""
        if not ids:
            return 0
        if not (len(ids) == len(documents) == len(embeddings) == len(metadatas)):
            raise ValueError("ids/documents/embeddings/metadatas 길이가 서로 다릅니다.")

        self._collection.upsert(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=[_clean_metadata(m) for m in metadatas],
        )
        return len(ids)

    def search(
        self,
        query: str,
        top_k: int = TOP_K,
        where: dict | None = None,
        min_similarity: float | None = MIN_SIMILARITY,
    ) -> list[dict]:
        """질의와 의미가 가까운 청크를 반환한다.

        where에 메타데이터 필터를 넘길 수 있다. 예: {"type": "항공"}
        날짜 범위는 정수 키를 쓴다. 예: {"date_start_int": {"$lte": 20261014}}

        min_similarity=None을 주면 임계값 없이 원본 순위를 그대로 돌려준다.
        """
        if self.count() == 0:
            return []

        result = self._collection.query(
            query_embeddings=[embed_query(query)],
            n_results=min(top_k, self.count()),
            where=where or None,
            include=["documents", "metadatas", "distances"],
        )

        hits = []
        for index, chunk_id in enumerate(result["ids"][0]):
            distance = result["distances"][0][index]
            hits.append(
                {
                    "id": chunk_id,
                    "document": result["documents"][0][index],
                    "metadata": result["metadatas"][0][index],
                    "distance": distance,
                    # 코사인 거리 → 유사도. 거리 0이 완전 일치.
                    "similarity": 1.0 - distance,
                }
            )

        return _apply_threshold(hits, min_similarity)

    def count(self) -> int:
        return self._collection.count()

    def indexed_sources(self) -> dict[str, str]:
        """이미 인덱싱된 {source_file: content_hash} 매핑."""
        if self.count() == 0:
            return {}
        records = self._collection.get(include=["metadatas"])
        return {
            metadata["source_file"]: metadata.get("content_hash", "")
            for metadata in records["metadatas"]
            if metadata.get("source_file")
        }

    def trip_date_range(self) -> tuple[str | None, str | None]:
        """인덱싱된 예약 전체에서 여행 시작일과 종료일을 구한다.

        'N일차'를 실제 날짜로 바꿀 때 기준이 된다.
        """
        if self.count() == 0:
            return None, None
        records = self._collection.get(include=["metadatas"])
        starts = [m["date"] for m in records["metadatas"] if m.get("date")]
        ends = [m.get("date_end") or m["date"] for m in records["metadatas"] if m.get("date")]
        if not starts:
            return None, None
        return min(starts), max(ends)

    def all_reservations(self) -> list[dict]:
        """인덱싱된 예약을 날짜순으로 반환한다(요약 카드용).

        메일 1건이 여러 청크로 나뉘어도 예약은 하나이므로 source_file 기준으로 묶는다.
        """
        if self.count() == 0:
            return []
        # 환불 규정 같은 본문 정보는 메타데이터가 아니라 청크에 들어 있다.
        records = self._collection.get(include=["metadatas", "documents"])

        by_source: dict[str, dict] = {}
        for index, metadata in enumerate(records["metadatas"]):
            source = metadata.get("source_file")
            if source and source not in by_source:
                by_source[source] = {**metadata, "document": records["documents"][index]}

        return sorted(
            by_source.values(),
            # 날짜가 없는 예약은 뒤로 보낸다. 같은 날이면 시각순
            # ('09:20 ~ 11:45'는 앞자리 HH:MM 문자열 비교로 정렬된다).
            key=lambda m: (
                m.get("date_start_int") or 99999999,
                m.get("time") or "99:99",
                m.get("provider") or "",
            ),
        )

    def delete_by_source(self, source_file: str) -> None:
        """한 메일에서 나온 청크를 모두 지운다(재인덱싱용)."""
        self._collection.delete(where={"source_file": source_file})

    def reset(self) -> None:
        """컬렉션을 통째로 비운다."""
        self._client.delete_collection(self.collection_name)
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            configuration={"hnsw": {"space": DISTANCE_METRIC}},
            embedding_function=None,
        )


def _apply_threshold(hits: list[dict], min_similarity: float | None) -> list[dict]:
    """무관한 결과를 걸러낸다.

    두 단계로 거른다.
    1) 절대 임계값: 질의 자체가 예약과 무관하면(예: "내 여권 번호") 전부 탈락.
    2) 상대 임계값: 1위 대비 크게 낮은 결과는 버린다. 예약 메일은 서로
       형식이 비슷해 유사도가 좁게 몰리므로, 절대값만으로는 "체크아웃"
       질의에 항공권이 딸려오는 것을 막지 못한다.
    """
    if min_similarity is None or not hits:
        return hits

    kept = [hit for hit in hits if hit["similarity"] >= min_similarity]
    if not kept:
        return []

    cutoff = kept[0]["similarity"] * RELATIVE_SIMILARITY_RATIO
    return [hit for hit in kept if hit["similarity"] >= cutoff]


def _clean_metadata(metadata: dict) -> dict:
    """Chroma는 None을 받지 않으므로 빈 값은 키째로 뺀다."""
    return {
        key: value
        for key, value in metadata.items()
        if value is not None and value != ""
    }
