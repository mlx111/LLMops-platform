from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import Base, engine
from app.routers import datasets, versions, runs, dashboard, config_router, traces, reports


def create_app() -> FastAPI:
    app = FastAPI(
        title="LLMOps Evaluation Platform",
        description="RAG/Agent LLMOps Evaluation & Observability Platform",
        version="0.1.0",
    )

    # Parse CORS origins from comma-separated env var (default "*")
    raw = settings.cors_origins or "*"
    origins = [o.strip() for o in raw.split(",") if o.strip()]

    cors_kwargs: dict = {
        "allow_origins": origins,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }
    # "*" and allow_credentials=True are mutually incompatible in browsers
    if origins != ["*"]:
        cors_kwargs["allow_credentials"] = True

    app.add_middleware(CORSMiddleware, **cors_kwargs)

    #Base.metadata.create_all(bind=engine)

    app.include_router(datasets.router)
    app.include_router(versions.router)
    app.include_router(runs.router)
    app.include_router(dashboard.router)
    app.include_router(config_router.router)
    app.include_router(traces.router)
    app.include_router(reports.router)

    @app.on_event("startup")
    def auto_seed_demo_data():
        """Seed demo datasets on first run if DB is empty."""
        from app.seed import seed

        Base.metadata.create_all(bind=engine)
        seed()

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
