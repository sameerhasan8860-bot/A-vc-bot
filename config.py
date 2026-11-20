from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str
    bot_token: str
    mongo_uri: str

    @classmethod
    def from_env(cls) -> "Settings":
        required = ("API_ID", "API_HASH", "BOT_TOKEN", "MONGO_DB")
        missing = [key for key in required if not os.getenv(key)]
        if missing:
            raise RuntimeError("Missing secrets: " + ", ".join(missing))
        try:
            api_id = int(os.environ["API_ID"])
        except ValueError as exc:
            raise RuntimeError("API_ID must be an integer") from exc
        return cls(
            api_id=api_id,
            api_hash=os.environ["API_HASH"].strip(),
            bot_token=os.environ["BOT_TOKEN"].strip(),
            mongo_uri=os.environ["MONGO_DB"].strip(),
        )
