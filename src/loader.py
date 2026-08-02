"""메일 파일(.txt/.eml)을 읽어 텍스트로 반환한다."""

from __future__ import annotations

import email
from email import policy
from pathlib import Path

from src.config import EMAILS_DIR, SUPPORTED_EXTENSIONS


def load_emails(directory: str | Path | None = None) -> list[dict]:
    """디렉터리 안의 메일 파일을 모두 읽어 리스트로 반환한다.

    각 항목은 {"filename": str, "raw_text": str} 형태이며 파일명 순으로 정렬된다.
    """
    directory = Path(directory) if directory is not None else EMAILS_DIR
    if not directory.is_dir():
        raise FileNotFoundError(f"메일 디렉터리를 찾을 수 없습니다: {directory}")

    emails = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        raw_text = read_email_file(path)
        if raw_text.strip():
            emails.append({"filename": path.name, "raw_text": raw_text})
    return emails


def read_email_file(path: str | Path) -> str:
    """단일 메일 파일을 텍스트로 읽는다. .eml은 헤더+본문을 평문으로 펼친다."""
    path = Path(path)
    if path.suffix.lower() == ".eml":
        return _read_eml(path)
    return path.read_text(encoding="utf-8", errors="replace")


def _read_eml(path: Path) -> str:
    """.eml에서 주요 헤더와 text/plain 본문을 추출한다.

    text/plain이 없으면 text/html 파트를 태그만 걷어낸 형태로 대체한다.
    """
    with path.open("rb") as fp:
        message = email.message_from_binary_file(fp, policy=policy.default)

    header_lines = [
        f"{name}: {message[name]}"
        for name in ("From", "To", "Subject", "Date")
        if message[name]
    ]

    body_part = message.get_body(preferencelist=("plain", "html"))
    if body_part is None:
        body = ""
    else:
        body = body_part.get_content()
        if body_part.get_content_type() == "text/html":
            body = _strip_html(body)

    return "\n".join(header_lines + ["", body]).strip()


def _strip_html(html: str) -> str:
    """HTML 본문에서 태그를 제거하고 엔티티를 푼다."""
    import html as html_module
    import re

    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_module.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
