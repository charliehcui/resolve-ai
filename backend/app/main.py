from fastapi import FastAPI

app = FastAPI(
    title="ResolveAI API",
    version="0.1.0",
)


@app.get("/health/live", tags=["health"])
def health_live() -> dict[str, str]:
    """Confirm that the API process is running."""
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def health_ready() -> dict[str, str]:
    """Confirm that the API is ready to receive requests."""
    return {"status": "ready"}
