.PHONY: help install run index ask eval test clean

help:
	@echo "make install   의존성 설치"
	@echo "make serve     웹앱 실행 (http://localhost:8000)"
	@echo "make index     샘플 메일 인덱싱"
	@echo "make ask Q=... 질문 하나 던지기"
	@echo "make eval      평가 실행"
	@echo "make test      테스트 실행"
	@echo "make clean     인덱스와 캐시 삭제"

install:
	pip install -r requirements.txt

serve:
	uvicorn api:app --reload --port 8000

index:
	python -m src.indexer --sample

ask:
	@python -m src.agent "$(Q)"

eval:
	python -m src.evaluate

test:
	pytest tests/ -v

clean:
	rm -rf data/chroma __pycache__ src/__pycache__ .pytest_cache
