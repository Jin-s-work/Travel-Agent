# chromadb 1.5.x의 공식 지원 범위가 3.13까지다. 3.14에서는 import가 멈춘다.
FROM python:3.13-slim

# Spaces는 컨테이너를 UID 1000으로 실행한다. root로 만든 파일에는 쓸 수 없으므로
# 같은 사용자를 만들어 두고 그 홈 아래에서 작업한다.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR $HOME/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user api.py .
COPY --chown=user src/ ./src/
COPY --chown=user web/ ./web/

# 업로드한 메일과 벡터 인덱스가 쌓이는 곳. 영구 저장은 아니다.
RUN mkdir -p data/emails data/chroma

# 호스트마다 포트 지정 방식이 다르다. Render는 PORT를 주입하고,
# 지정이 없으면 7860(Spaces 관례)으로 뜬다. 셸 형식이라야 변수가 확장된다.
EXPOSE 7860
CMD uvicorn api:app --host 0.0.0.0 --port ${PORT:-7860}
