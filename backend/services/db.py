from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from config import get_settings

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


async def connect():
    global _client, _db
    s = get_settings()
    _client = AsyncIOMotorClient(
        s.MONGODB_URI,
        maxPoolSize=50,
        minPoolSize=2,
        maxIdleTimeMS=300_000,
        serverSelectionTimeoutMS=5_000,
    )
    _db = _client[s.MONGODB_DB]
    await _db.participants.create_index("token", unique=True)
    await _db.participants.create_index("email", unique=True)
    await _db.responses.create_index("token")
    await _db.participants_backup.create_index("token")
    await _db.participants_backup.create_index([("token", 1), ("version", -1)])
    await _db.admins.create_index("token", unique=True)
    await _db.admins.create_index("email", unique=True)
    await _db.email_logs.create_index([("token", 1), ("sent_at", -1)])
    await _db.email_logs.create_index([("sent_at", -1)])
    await _db.email_logs.create_index("batch_id")
    await _db.email_logs.create_index("status")
    await _db.review_comments.create_index("id", unique=True)
    await _db.review_comments.create_index([("survey_version", 1), ("qid", 1), ("created_at", 1)])
    await _db.review_comments.create_index("author_token")
    await _db.review_comments.create_index("parent_id")


async def disconnect():
    global _client
    if _client:
        _client.close()


def get_db() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("Database not connected")
    return _db
