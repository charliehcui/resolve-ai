from backend.app.config import PROJECT_ROOT


def test_v2_runtime_has_no_v1_dependency() -> None:
    runtime_files = []
    for folder in ("backend", "simulator"):
        runtime_files.extend(path for path in (PROJECT_ROOT / folder).rglob("*.py") if "__pycache__" not in path.parts)
    runtime_files.extend(PROJECT_ROOT / name for name in ("Dockerfile", "Dockerfile.api", "Dockerfile.init", "compose.yaml", "pyproject.toml"))
    forbidden = ("../V1", "..\\V1", "resolve-ai/V1", "resolve-ai\\V1", "from V1", "import V1")
    references = {str(path.relative_to(PROJECT_ROOT)): value for path in runtime_files for value in forbidden if value in path.read_text(encoding="utf-8")}
    assert references == {}


def test_support_image_excludes_lab_and_evaluation_assets() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile.api").read_text(encoding="utf-8")
    assert "COPY simulator/lab" not in dockerfile
    assert "COPY evals" not in dockerfile
    assert "holdout" not in dockerfile.lower()


def test_example_environment_disables_remote_tracing() -> None:
    values = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    assert "LANGSMITH_TRACING=false" in values
