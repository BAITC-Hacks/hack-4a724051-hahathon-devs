"""Conversation-scoped context. References are published only with completed turns."""
import json
import sqlite3
from contextlib import closing


class AssistantState:
    def __init__(self, path):
        self.path = path

    def previous(self, session_id, conversation_id):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("""SELECT t.output FROM turns t JOIN conversations c ON c.id=t.conversation_id
                WHERE c.session_id=? AND c.id=? AND t.status='completed' AND t.output IS NOT NULL
                ORDER BY t.created_at DESC LIMIT 1""", (session_id, conversation_id)).fetchone()
        return json.loads(row[0]) if row else {}
