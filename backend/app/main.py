from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.classification import ClassificationRequest, ClassificationResult, classify_ticket
from app.db.database import engine

app = FastAPI(
    title="ResolveAI API",
    version="0.1.0",
)


@app.get("/health/live", tags=["health"])
def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def health_ready() -> dict[str, str]:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Database is not ready")

    return {"status": "ready"}


@app.post("/api/v1/classification", response_model=ClassificationResult, tags=["classification"])
def create_classification(request: ClassificationRequest) -> ClassificationResult:
    try:
        return classify_ticket(request)
    except Exception as error:
        raise HTTPException(status_code=502, detail="Ticket classification failed") from error
