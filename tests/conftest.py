"""테스트는 실제 인덱스와 메일을 건드리지 않는다.

인덱스 비우기 엔드포인트를 검사하는 테스트가 실제 data/chroma에 DELETE를 보내
개발 중이던 인덱스를 통째로 날린 적이 있다. 임시 디렉터리로 돌린다.

os.environ는 src.config가 임포트되기 전에 설정해야 한다. pytest가 conftest를
테스트 모듈보다 먼저 읽으므로 이 파일의 최상단이 그 자리다.
"""

import os
import tempfile
from pathlib import Path

_SANDBOX = Path(tempfile.mkdtemp(prefix="travel-inbox-tests-"))
os.environ["CHROMA_DIR"] = str(_SANDBOX / "chroma")
os.environ["EMAILS_DIR"] = str(_SANDBOX / "emails")

import pytest  # noqa: E402  (환경변수 설정 뒤에 와야 한다)


@pytest.fixture(scope="session", autouse=True)
def sandbox_paths():
    """설정이 임시 경로를 보고 있는지 확인하고, 끝나면 지운다."""
    import shutil

    from src.config import CHROMA_DIR, EMAILS_DIR

    assert _SANDBOX in CHROMA_DIR.parents, "테스트가 실제 인덱스를 보고 있습니다"
    assert _SANDBOX in EMAILS_DIR.parents, "테스트가 실제 메일 디렉터리를 보고 있습니다"

    yield _SANDBOX
    shutil.rmtree(_SANDBOX, ignore_errors=True)
