"""Small private checkpoints/receipts in PostgreSQL, committed with their guard."""
from src.foundation.repository import DomainError


class Artifacts:
    def __init__(self, db): self.db = db

    def put(self, con, ref, scope, content):
        if len(content) > 32 * 1024 * 1024:
            raise DomainError('ARTIFACT_TOO_LARGE', '작업 결과 저장 상한을 넘었습니다.', 413)
        con.execute('INSERT INTO durable_artifacts(ref,scope_id,content) VALUES(?,?,?) '
                    'ON CONFLICT(ref) DO UPDATE SET content=excluded.content', (ref, scope, content))
        return ref

    def read(self, ref):
        with self.db.connect() as con:
            row = con.execute('SELECT content FROM durable_artifacts WHERE ref=?', (ref,)).fetchone()
        return bytes(row['content']) if row else None

    def delete(self, ref):
        with self.db.connect() as con:
            con.execute('DELETE FROM durable_artifacts WHERE ref=?', (ref,))

    def delete_scope(self, con, scope):
        con.execute('DELETE FROM durable_artifacts WHERE scope_id=?', (scope,))
