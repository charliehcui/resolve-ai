from app.db.database import SessionLocal
from app.db.models import SupportSession


def create_support_session_record(session_id: str, customer_id: str, thread_id: str) -> None:
    support_session = SupportSession()
    support_session.session_id = session_id
    support_session.customer_id = customer_id
    support_session.thread_id = thread_id
    support_session.status = "started"
    support_session.final_problem_details = None

    with SessionLocal() as database:
        database.add(support_session)
        database.commit()


def save_support_session_progress(session_id: str, status: str, problem_details: dict[str, object] | None) -> None:
    with SessionLocal() as database:
        support_session = database.get(SupportSession, session_id)

        if support_session is None:
            raise ValueError("Support session not found")

        support_session.status = status
        support_session.final_problem_details = problem_details
        database.commit()
