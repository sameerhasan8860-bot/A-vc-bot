import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web

from bot import get_runtime
from dsp import DSPSettings, AudioDSP

log = logging.getLogger("chimney.miniapp")
BOT_TOKEN = os.environ["BOT_TOKEN"]
WEB_DIR = Path(__file__).parent / "webapp"


def validate_init_data(raw: str):
    if not raw or len(raw) > 8192:
        raise web.HTTPUnauthorized(text="Missing Telegram WebApp authentication.")
    data = dict(parse_qsl(raw, keep_blank_values=True))
    received_hash = data.pop("hash", None)
    if not received_hash:
        raise web.HTTPUnauthorized(text="Invalid Telegram WebApp authentication.")
    auth_date = int(data.get("auth_date", "0"))
    if auth_date <= 0 or time.time() - auth_date > 86400 or auth_date > time.time() + 60:
        raise web.HTTPUnauthorized(text="Telegram session expired. Reopen the Mini App.")
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise web.HTTPUnauthorized(text="Telegram authentication could not be verified.")
    try:
        user = json.loads(data["user"])
        user_id = int(user["id"])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        raise web.HTTPUnauthorized(text="Invalid Telegram user.")
    return user_id


@web.middleware
async def auth_middleware(request, handler):
    if request.path.startswith("/api/"):
        request["user_id"] = validate_init_data(request.headers.get("X-Telegram-Init-Data", ""))
    return await handler(request)


def runtime_or_error(user_id):
    runtime = get_runtime(user_id)
    if runtime is None:
        raise web.HTTPConflict(text="Account not hosted. Send /host to the bot first.")
    return runtime


async def state(request):
    runtime = get_runtime(request["user_id"])
    if runtime is None:
        return web.json_response({"hosted": False, "bridge": None, "dsp": None, "level": 1})
    bridge = runtime.bridge
    return web.json_response({
        "hosted": True,
        "bridge": ({
            "source_id": bridge.source_id, "target_id": bridge.target_id,
            "source_name": bridge.source_name, "target_name": bridge.target_name,
        } if bridge else None),
        "level": runtime.level,
        "dsp": runtime.dsp.settings.to_dict(),
    })


async def update_settings(request):
    runtime = runtime_or_error(request["user_id"])
    try:
        body = await request.json()
        allowed = {
            "enabled", "amplification_db", "gain_percent", "bass_percent",
            "treble_percent", "echo_percent", "distortion_percent",
        }
        changes = {k: v for k, v in body.items() if k in allowed}
        if not changes:
            raise ValueError("No valid audio settings supplied.")
        await runtime.update_dsp(**changes)
        db = request.app["db"]
        await db.save_dsp(request["user_id"], runtime.dsp.settings.to_dict())
        return web.json_response({"ok": True, "dsp": runtime.dsp.settings.to_dict()})
    except (ValueError, TypeError) as exc:
        raise web.HTTPBadRequest(text=str(exc))


async def update_level(request):
    runtime = runtime_or_error(request["user_id"])
    try:
        body = await request.json()
        value = float(body.get("level"))
        if value < 0 or value > 100 or value != value or value in (float("inf"), float("-inf")):
            raise ValueError("Level must be between 0 and 100.")
        await runtime.set_level(value)
        await request.app["db"].save_level(request["user_id"], value)
        return web.json_response({"ok": True, "level": value})
    except (ValueError, TypeError) as exc:
        raise web.HTTPBadRequest(text=str(exc))


async def join_bridge(request):
    runtime = runtime_or_error(request["user_id"])
    try:
        body = await request.json()
        source_id, target_id = int(body["source_id"]), int(body["target_id"])
        info = await runtime.start_bridge(source_id, target_id)
        await request.app["db"].save_bridge(request["user_id"], source_id, target_id, runtime.level)
        return web.json_response({
            "ok": True, "bridge": {
                "source_id": info.source_id, "target_id": info.target_id,
                "source_name": info.source_name, "target_name": info.target_name,
            }
        })
    except (KeyError, ValueError, TypeError) as exc:
        raise web.HTTPBadRequest(text=f"Invalid group IDs: {exc}")
    except Exception as exc:
        log.exception("Mini App bridge start failed")
        raise web.HTTPBadRequest(text=str(exc)[:500])



async def unhost_account(request):
    user_id = request["user_id"]
    runtime = get_runtime(user_id)
    if runtime is not None:
        from bot import runtimes
        runtimes.pop(user_id, None)
        await runtime.stop()
    await request.app["db"].delete_account(user_id)
    return web.json_response({"ok": True})


async def leave_bridge(request):
    runtime = runtime_or_error(request["user_id"])
    await runtime.stop_bridge()
    await request.app["db"].clear_bridge(request["user_id"])
    return web.json_response({"ok": True})


async def index(_request):
    return web.FileResponse(WEB_DIR / "index.html")


def create_app(db):
    app = web.Application(middlewares=[auth_middleware], client_max_size=16 * 1024)
    app["db"] = db
    app.router.add_get("/", index)
    app.router.add_get("/api/state", state)
    app.router.add_post("/api/settings", update_settings)
    app.router.add_post("/api/level", update_level)
    app.router.add_post("/api/bridge", join_bridge)
    app.router.add_post("/api/leave", leave_bridge)\n    app.router.add_post("/api/unhost", unhost_account)
    return app
