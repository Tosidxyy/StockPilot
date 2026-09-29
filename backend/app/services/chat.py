"""Store visible Agent conversations."""

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.database.models import ChatMessage, ChatSession, ChatEvidence


class ChatNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class ChatTurn:
    role: str
    content: str


class ChatService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def messages(self, session_id: str) -> list[ChatTurn]:
        with self._session_factory() as session:
            if session.get(ChatSession, session_id) is None:
                raise ChatNotFoundError(session_id)
            rows = session.scalars(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.id)
            ).all()
            return [ChatTurn(role=row.role, content=row.content) for row in rows]

    def ensure_session(self, session_id: str | None, prompt: str) -> str:
        with self._session_factory.begin() as session:
            if session_id is None:
                session_id = str(uuid4())
                session.add(ChatSession(id=session_id, title=prompt[:200]))
            elif session.get(ChatSession, session_id) is None:
                raise ChatNotFoundError(session_id)
        return session_id

    def save_exchange(self, session_id: str, prompt: str, answer: str, evidence: list[dict] | None = None) -> None:
        with self._session_factory.begin() as session:
            if session.get(ChatSession, session_id) is None:
                raise ChatNotFoundError(session_id)
            assistant = ChatMessage(session_id=session_id, role="assistant", content=answer)
            session.add_all([
                ChatMessage(session_id=session_id, role="user", content=prompt),
                assistant,
            ])
            session.flush()
            session.add_all([ChatEvidence(message_id=assistant.id,evidence_id=item["evidence_id"],payload=item) for item in (evidence or [])])

    def evidence(self, session_id: str) -> list[dict]:
        with self._session_factory() as session:
            if session.get(ChatSession, session_id) is None:
                raise ChatNotFoundError(session_id)
            rows = session.execute(select(ChatEvidence).join(ChatMessage).where(
                ChatMessage.session_id==session_id).order_by(ChatMessage.id,ChatEvidence.evidence_id)).scalars().all()
            return [{"message_id":row.message_id,**row.payload,"historical_snapshot":True} for row in rows]
