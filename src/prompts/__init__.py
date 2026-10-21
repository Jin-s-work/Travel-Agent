"""Versioned prompt registry: one `src/prompts/<id>.md` per prompt.

Front matter (`id`, `version`, `purpose`, `output_schema`, `variables`) is
followed by the body, which is used verbatim — including whether it ends with a
newline — because the exact bytes are part of every provider request hash.
`sha256` is over the body template; `{{NAME}}` placeholders are the only
substitution, so literal braces such as `{provider}` stay untouched.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
from pathlib import Path
import re

PROMPT_DIR = Path(__file__).resolve().parent
_ID = re.compile(r'[a-z][a-z0-9_]{2,63}')
_PLACEHOLDER = re.compile(r'\{\{([A-Z][A-Z0-9_]*)\}\}')
_REQUIRED = ('id', 'version', 'purpose', 'output_schema')


@dataclass(frozen=True)
class Prompt:
    id: str
    version: int
    purpose: str
    output_schema: str
    variables: tuple[str, ...]
    body: str
    sha256: str

    def render(self, **values: str) -> str:
        if set(values) != set(self.variables):
            raise ValueError(f'{self.id}: 변수 {sorted(self.variables)}가 정확히 필요합니다.')
        return _PLACEHOLDER.sub(lambda match: values[match.group(1)], self.body)

    def meta(self) -> dict:
        """What results, ledgers and pipeline signatures record. Never the body."""
        return {'id': self.id, 'version': self.version, 'hash': self.sha256}


def _parse(text: str, prompt_id: str) -> Prompt:
    if not text.startswith('---\n'):
        raise ValueError(f'{prompt_id}: front matter가 없습니다.')
    end = text.find('\n---\n', 4)
    if end < 0:
        raise ValueError(f'{prompt_id}: front matter가 닫히지 않았습니다.')
    fields = {}
    for line in text[4:end].splitlines():
        key, separator, value = line.partition(':')
        if not separator or not key.strip():
            raise ValueError(f'{prompt_id}: front matter 줄을 읽을 수 없습니다.')
        fields[key.strip()] = value.strip()
    missing = [key for key in _REQUIRED if not fields.get(key)]
    if missing or fields['id'] != prompt_id:
        raise ValueError(f'{prompt_id}: front matter 필드가 올바르지 않습니다 ({missing or "id"}).')
    body = text[end + len('\n---\n'):]
    variables = tuple(name.strip() for name in fields.get('variables', '').split(',') if name.strip())
    used = set(_PLACEHOLDER.findall(body))
    if used != set(variables):
        raise ValueError(f'{prompt_id}: 본문 변수 {sorted(used)}와 선언 {sorted(variables)}가 다릅니다.')
    return Prompt(id=prompt_id, version=int(fields['version']), purpose=fields['purpose'],
                  output_schema=fields['output_schema'], variables=variables, body=body,
                  sha256=hashlib.sha256(body.encode('utf-8')).hexdigest())


@lru_cache(maxsize=None)
def load_prompt(prompt_id: str) -> Prompt:
    if not _ID.fullmatch(prompt_id):
        raise ValueError('프롬프트 id 형식이 올바르지 않습니다.')
    path = PROMPT_DIR / f'{prompt_id}.md'
    # newline='' keeps the body byte-for-byte; universal newlines would rewrite CRLF.
    with path.open(encoding='utf-8', newline='') as stream:
        return _parse(stream.read(), prompt_id)


def registry() -> dict[str, dict]:
    """All registered prompts' metadata, for reports and admin views."""
    result = {}
    for path in sorted(PROMPT_DIR.glob('*.md')):
        prompt = load_prompt(path.stem)
        result[prompt.id] = {**prompt.meta(), 'purpose': prompt.purpose, 'output_schema': prompt.output_schema}
    return result
