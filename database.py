import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import ConfigurationError


class Database:
    def __init__(self, mongo_uri: str, api_hash: str, bot_token: str):
        self.client = AsyncIOMotorClient(
            mongo_uri, serverSelectionTimeoutMS=15000, connectTimeoutMS=15000
        )
        try:
            self.db = self.client.get_default_database()
        except ConfigurationError:
            self.db = self.client["chimney_vc_bot"]

        # No fifth secret: encryption key is derived from two existing secrets.
        # Rotating either secret means users must host their accounts again.
        material = hashlib.sha256(
            f"CHIMNEY-VC|{api_hash}|{bot_token}".encode()
        ).digest()
        self.fernet = Fernet(base64.urlsafe_b64encode(material))
        self.sessions = self.db["hosted_sessions"]

    async def connect(self):
        await self.client.admin.command("ping")
        await self.sessions.create_index("user_id", unique=True)

    def encrypt(self, value: str) -> str:
        return self.fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self.fernet.decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise RuntimeError(
                "Session decryption failed. API_HASH or BOT_TOKEN may have changed."
            ) from exc

    async def save_session(self, user_id: int, session: str):
        await self.sessions.update_one(
            {"user_id": user_id},
            {"$set": {"session_enc": self.encrypt(session)},
             "$setOnInsert": {"user_id": user_id, "bridge": None, "level": 1.0}},
            upsert=True,
        )

    async def all_accounts(self):
        docs = await self.sessions.find({}).to_list(length=None)
        for doc in docs:
            doc["session"] = self.decrypt(doc.pop("session_enc"))
        return docs

    async def save_bridge(self, user_id: int, source_id: int, target_id: int, level: float):
        await self.sessions.update_one(
            {"user_id": user_id},
            {"$set": {"bridge": {"source_id": source_id, "target_id": target_id},
                      "level": level}},
        )

    async def save_level(self, user_id: int, level: float):
        await self.sessions.update_one({"user_id": user_id}, {"$set": {"level": level}})

    async def save_dsp(self, user_id: int, settings: dict):
        await self.sessions.update_one({"user_id": user_id}, {"$set": {"dsp": settings}})

    async def clear_bridge(self, user_id: int):
        await self.sessions.update_one({"user_id": user_id}, {"$set": {"bridge": None}})

    async def delete_account(self, user_id: int):
        await self.sessions.delete_one({"user_id": user_id})

    async def close(self):
        self.client.close()
