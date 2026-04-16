from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from config import get_settings
from services import db
from routers import responses, participants


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    yield
    await db.disconnect()


app = FastAPI(
    title="AURI AI Survey API",
    version="1.0.0",
    lifespan=lifespan,
)

s = get_settings()
origins = [o.strip() for o in s.CORS_ORIGINS.split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(responses.router)
app.include_router(participants.router)


@app.get("/api/health")
async def health():
    try:
        database = db.get_db()
        await database.command("ping")
        return {"status": "ok", "db": "connected"}
    except Exception as e:
        return {"status": "degraded", "db": str(e)}
