from app.db.database import SessionLocal
from app.db.models import SupportSession


def create_support_session_record(session_id: str, customer_id: str, thread_id: str) -> None:
    support_session = SupportSession()
    support_session.session_id = session_id
    support_session.customer_id = customer_id
    support_session.thread_id = thread_id
    support_session.status = "started"
    support_session.final_problem_details = None
    support_session.customer_result = None

    with SessionLocal() as database:
        database.add(support_session)
        database.commit()


def get_support_session_thread_id(session_id: str) -> str:
    with SessionLocal() as database:
        support_session = database.get(SupportSession, session_id)

        if support_session is None:
            raise ValueError("Support session not found")

        return support_session.thread_id


def get_support_session_result(session_id: str) -> tuple[str, str | None]:
    with SessionLocal() as database:
        support_session = database.get(SupportSession, session_id)

        if support_session is None:
            raise ValueError("Support session not found")

        return support_session.status, support_session.customer_result


def save_support_session_progress(session_id: str, status: str, problem_details: dict[str, object] | None, customer_result: str | None) -> None:
    with SessionLocal() as database:
        support_session = database.get(SupportSession, session_id)

        if support_session is None:
            raise ValueError("Support session not found")

        support_session.status = status
        support_session.final_problem_details = problem_details
        support_session.customer_result = customer_result
        database.commit()
