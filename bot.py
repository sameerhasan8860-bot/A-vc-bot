import html
import logging
import math
import os
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

from config import Settings
from database import Database
from voice import VoiceRuntime
from dsp import AudioDSP, DSPSettings

log = logging.getLogger("chimney.bot")
router = Router()
runtimes: dict[int, VoiceRuntime] = {}
pending_logins: dict[int, dict[str, Any]] = {}


def home_keyboard(include_mini_app=True):
    rows = []
    mini_app_url = os.getenv("MINI_APP_URL", "").strip()
    if include_mini_app and mini_app_url.startswith("https://"):
        rows.append([InlineKeyboardButton(text="🎛 Open VC Control Panel", web_app=WebAppInfo(url=mini_app_url))])
    rows.extend([
        [
            InlineKeyboardButton(text="🎙 Host Account", callback_data="host"),
            InlineKeyboardButton(text="📊 Live Status", callback_data="status"),
        ],
        [
            InlineKeyboardButton(text="📖 Command Guide", callback_data="help"),
            InlineKeyboardButton(text="⏹ Leave Bridge", callback_data="leave"),
        ],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def help_text():
    return (
        "╭━━━ <b>CHIMNEY VC BOT</b> ━━━╮\n"
        "│ <i>Voice Chat Bridge Console</i>\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "<b>ACCOUNT</b>\n"
        "▸ /host — Host your Telegram account\n"
        "▸ /unhost — Remove hosted account and session\n\n"
        "<b>VOICE BRIDGE</b>\n"
        "▸ /join &lt;source_id&gt; &lt;target_id&gt;\n"
        "▸ /leave — Disconnect both voice chats\n"
        "▸ /level &lt;number&gt; — Legacy outgoing gain\n"
        "▸ /amp on|off or /amp 0-200 — Amplification dB\n"
        "▸ /gain, /bass, /treble, /echo, /distortion 0-100\n"
        "▸ /dsp — Show audio DSP settings\n"
        "▸ /status — Show account and bridge status\n\n"
        "<b>GENERAL</b>\n"
        "▸ /start — Open the control panel\n"
        "▸ /help — Show this command guide\n"
        "▸ /cancel — Cancel the current hosting login\n\n"
        "<i>Example: /join -1001111111111 -1002222222222</i>\n"
        "<i>Order: SOURCE first, TARGET second.</i>"
    )


def get_runtime(user_id: int):
    return runtimes.get(user_id)


def status_text(runtime):
    if runtime is None:
        return (
            "╭━━ <b>CHIMNEY • STATUS</b> ━━╮\n"
            "│ Account: <code>NOT HOSTED</code>\n"
            "│ Bridge: <code>INACTIVE</code>\n"
            "╰━━━━━━━━━━━━━━━━━━╯\n\n"
            "Use /host to connect your Telegram account."
        )

    bridge = runtime.bridge
    lines = [
        "╭━━ <b>CHIMNEY • STATUS</b> ━━╮",
        "│ Account: <code>HOSTED ✓</code>",
        f"│ Bridge: <code>{'ACTIVE ✓' if bridge else 'INACTIVE'}</code>",
    ]
    if bridge:
        lines.extend([
            "├─ <b>SOURCE</b>",
            f"│  {html.escape(bridge.source_name)}",
            f"│  <code>{bridge.source_id}</code>",
            "├─ <b>TARGET</b>",
            f"│  {html.escape(bridge.target_name)}",
            f"│  <code>{bridge.target_id}</code>",
        ])
    lines.extend([
        f"├─ Audio gain: <code>{runtime.level:g}x</code>",
        "╰━━━━━━━━━━━━━━━━━━╯",
    ])
    return "\n".join(lines)


@router.message(Command("start"))
async def start_command(message: Message):
    if message.chat.type != "private":
        await message.reply("Please open my private chat to use CHIMNEY VC BOT.")
        return
    text = (
        "╭━━━ <b>CHIMNEY VC BOT</b> ━━━╮\n"
        "│ <i>Live Voice Chat Bridge</i>\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
        "Welcome to your personal VC control panel.\n"
        "Host your Telegram account, connect two voice chats and relay "
        "source audio to your target group.\n\n"
        f"{status_text(get_runtime(message.from_user.id))}\n\n"
        "<i>Use the buttons below to get started.</i>"
    )
    await message.answer(text, reply_markup=home_keyboard())


@router.message(Command("help"))
async def help_command(message: Message):
    await message.answer(help_text(), reply_markup=home_keyboard(message.chat.type == "private"))


@router.callback_query(F.data == "help")
async def help_callback(callback: CallbackQuery):
    await callback.message.answer(help_text(), reply_markup=home_keyboard(callback.message.chat.type == "private"))
    await callback.answer()


@router.callback_query(F.data == "status")
async def status_callback(callback: CallbackQuery):
    await callback.message.answer(status_text(get_runtime(callback.from_user.id)))
    await callback.answer()


@router.callback_query(F.data == "host")
async def host_callback(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer("Send /host in this private chat to begin account hosting.")


@router.callback_query(F.data == "leave")
async def leave_callback(callback: CallbackQuery, db: Database):
    runtime = get_runtime(callback.from_user.id)
    if runtime is None:
        await callback.answer("No hosted account.", show_alert=True)
        return
    await runtime.stop_bridge()
    await db.clear_bridge(callback.from_user.id)
    await callback.answer("Bridge disconnected.")
    await callback.message.answer(status_text(runtime))



def dsp_panel(runtime):
    s=runtime.dsp.settings
    return ("<b>CHIMNEY • AUDIO DSP</b>\\n"
      f"Power: <code>{'ON' if s.enabled else 'OFF'}</code>\\n"
      f"Amplification: <code>{s.amplification_db:g} dB</code>\\n"
      f"Gain: <code>{s.gain_percent:g}%</code> • Bass: <code>{s.bass_percent:g}%</code>\\n"
      f"Treble: <code>{s.treble_percent:g}%</code> • Echo: <code>{s.echo_percent:g}%</code>\\n"
      f"Distortion: <code>{s.distortion_percent:g}%</code>")

async def apply_dsp(message, runtime, db, **changes):
    try:
        await runtime.update_dsp(**changes)
        await db.save_dsp(message.from_user.id,runtime.dsp.settings.to_dict())
        await message.answer(dsp_panel(runtime))
    except ValueError as exc:
        await message.answer(f"Invalid value: {html.escape(str(exc))}")

@router.message(Command("host"))
async def host_command(message: Message, settings: Settings):
    if message.chat.type != "private":
        await message.reply("For account security, use /host in my private chat.")
        return
    user_id = message.from_user.id
    if user_id in runtimes:
        await message.answer("Your Telegram account is already hosted. Use /status.")
        return
    if user_id in pending_logins:
        await message.answer("A login is already in progress. Send /cancel first if needed.")
        return

    client = TelegramClient(StringSession(), settings.api_id, settings.api_hash)
    try:
        await client.connect()
        await message.answer(
            "╭━━ <b>HOST ACCOUNT</b> ━━╮\n"
            "Send your phone number with country code.\n"
            "Example: <code>+911234567890</code>\n\n"
            "<i>Your login code and 2FA password are used only to create your "
            "Telegram session. Never share them with another person.</i>"
        )
        pending_logins[user_id] = {
            "step": "phone",
            "client": client,
        }
    except Exception:
        await client.disconnect()
        log.exception("Could not start login flow for %s", user_id)
        await message.answer("Could not start Telegram login. Please try again later.")



@router.message(F.web_app_data)
async def miniapp_message_handler(message: Message, settings: Settings):
    if message.chat.type == "private" and message.web_app_data and message.web_app_data.data == "host":
        await host_command(message, settings)
\n@router.message(Command("cancel"))
async def cancel_command(message: Message):
    pending = pending_logins.pop(message.from_user.id, None)
    if pending:
        try:
            await pending["client"].disconnect()
        except Exception:
            pass
        await message.answer("Login cancelled.")
    else:
        await message.answer("There is no pending login.")


async def finish_hosting(
    message: Message,
    user_id: int,
    client: TelegramClient,
    db: Database,
    settings: Settings,
):
    session = client.session.save()
    await client.disconnect()
    runtime = VoiceRuntime(user_id, session, settings)
    await runtime.start()
    runtimes[user_id] = runtime
    await db.save_session(user_id, session)
    pending_logins.pop(user_id, None)
    await message.answer(
        "╭━━ <b>HOST SUCCESSFUL</b> ━━╮\n"
        "│ Telegram account: <code>CONNECTED ✓</code>\n"
        "│ Session: <code>ENCRYPTED IN MONGODB</code>\n"
        "╰━━━━━━━━━━━━━━━━━━╯\n\n"
        "Now use /join SOURCE_ID TARGET_ID."
    )


@router.message(F.text & ~F.text.startswith("/"))
async def login_text_handler(
    message: Message,
    db: Database,
    settings: Settings,
):
    if message.chat.type != "private" or not message.from_user:
        return
    text = (message.text or "").strip()
    if text.startswith("/"):
        return
    user_id = message.from_user.id
    pending = pending_logins.get(user_id)
    if not pending:
        return
    # Keep phone numbers, login codes and 2FA passwords out of chat history.
    try:
        await message.delete()
    except Exception:
        pass

    client: TelegramClient = pending["client"]
    try:
        if pending["step"] == "phone":
            phone = text.replace(" ", "")
            if not phone.startswith("+") or not phone[1:].isdigit():
                await message.answer("Invalid format. Send phone like <code>+911234567890</code>.")
                return
            sent = await client.send_code_request(phone)
            pending.update({
                "step": "code",
                "phone": phone,
                "phone_code_hash": sent.phone_code_hash,
            })
            await message.answer(
                "Telegram login code sent. Enter the code here.\n"
                "<i>Never forward this code to anyone.</i>"
            )
            return

        if pending["step"] == "code":
            code = text.replace(" ", "").replace("-", "")
            try:
                await client.sign_in(
                    phone=pending["phone"],
                    code=code,
                    phone_code_hash=pending["phone_code_hash"],
                )
            except SessionPasswordNeededError:
                pending["step"] = "password"
                await message.answer("Two-step verification is enabled. Send your Telegram 2FA password.")
                return
            await finish_hosting(message, user_id, client, db, settings)
            return

        if pending["step"] == "password":
            await client.sign_in(password=text)
            await finish_hosting(message, user_id, client, db, settings)
            return

    except PhoneNumberInvalidError:
        await message.answer("Telegram rejected that phone number. Send it again with country code.")
    except PhoneCodeInvalidError:
        await message.answer("Incorrect login code. Please try again.")
    except PhoneCodeExpiredError:
        await message.answer("Login code expired. Send /cancel and restart /host.")
    except FloodWaitError as exc:
        await message.answer(f"Telegram rate limit. Try again after {exc.seconds} seconds.")
    except Exception:
        log.exception("Login flow failed for %s", user_id)
        await message.answer("Login failed. Send /cancel and start /host again.")


@router.message(Command("join"))
async def join_command(message: Message, db: Database):
    runtime = get_runtime(message.from_user.id)
    if runtime is None:
        await message.answer("First host your account with /host.")
        return
    parts = (message.text or "").split()
    if len(parts) != 3:
        await message.answer(
            "Usage: <code>/join SOURCE_GROUP_ID TARGET_GROUP_ID</code>\n"
            "Source first, target second."
        )
        return
    try:
        source_id, target_id = int(parts[1]), int(parts[2])
        info = await runtime.start_bridge(source_id, target_id)
        await db.save_bridge(message.from_user.id, source_id, target_id, runtime.level)
        await message.answer(
            "╭━━ <b>BRIDGE CONNECTED</b> ━━╮\n"
            f"│ SOURCE: {html.escape(info.source_name)}\n"
            f"│ <code>{source_id}</code>\n"
            f"│ TARGET: {html.escape(info.target_name)}\n"
            f"│ <code>{target_id}</code>\n"
            "╰━━━━━━━━━━━━━━━━━━╯\n\n"
            f"Audio relay is live • Gain: <code>{runtime.level:g}x</code>"
        )
    except ValueError as exc:
        await message.answer(f"Invalid group IDs: {html.escape(str(exc))}")
    except Exception as exc:
        log.exception("Bridge start failed for %s", message.from_user.id)
        await message.answer(
            "Could not connect the bridge. Confirm the hosted account can access "
            "both groups and both voice chats are active.\n\n"
            f"<code>{html.escape(str(exc)[:700])}</code>"
        )



@router.message(Command("amp"))
async def amp_command(message: Message, db: Database):
    r=get_runtime(message.from_user.id)
    if not r: await message.answer("First host with /host."); return
    p=(message.text or "").split(maxsplit=1)
    if len(p)!=2: await message.answer("Usage: /amp on|off or /amp 0-200"); return
    x=p[1].lower()
    if x in ("on","off"): await apply_dsp(message,r,db,enabled=x=="on"); return
    try: await apply_dsp(message,r,db,amplification_db=float(x))
    except ValueError: await message.answer("Amplification must be 0-200 dB.")

def slider_cmd(field,label):
    async def handler(message: Message, db: Database):
        r=get_runtime(message.from_user.id)
        if not r: await message.answer("First host with /host."); return
        p=(message.text or "").split(maxsplit=1)
        if len(p)!=2: await message.answer(f"Usage: /{label.lower()} 0-100"); return
        try:
            x=float(p[1])
            if not math.isfinite(x): raise ValueError()
            await apply_dsp(message,r,db,**{field:x})
        except ValueError: await message.answer(f"{label} must be 0-100.")
    return handler

router.message.register(slider_cmd("gain_percent","Gain"),Command("gain"))
router.message.register(slider_cmd("bass_percent","Bass"),Command("bass"))
router.message.register(slider_cmd("treble_percent","Treble"),Command("treble"))
router.message.register(slider_cmd("echo_percent","Echo"),Command("echo"))
router.message.register(slider_cmd("distortion_percent","Distortion"),Command("distortion"))

@router.message(Command("dsp"))
async def dsp_command(message: Message):
    r=get_runtime(message.from_user.id)
    await message.answer(dsp_panel(r) if r else "First host with /host.")

@router.message(Command("level"))
async def level_command(message: Message, db: Database):
    runtime = get_runtime(message.from_user.id)
    if runtime is None:
        await message.answer("First host your account with /host.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer(
            f"Current gain: <code>{runtime.level:g}x</code>\n"
            "Usage: <code>/level 10</code>\n"
            "1 = original • 10 = 10x gain • 0 = mute"
        )
        return
    try:
        level = float(parts[1])
        if not math.isfinite(level) or level < 0:
            raise ValueError
        await runtime.set_level(level)
        await db.save_level(message.from_user.id, level)
        await message.answer(
            f"Audio gain updated: <code>{level:g}x</code>\n"
            "<i>Very high gain can cause clipping or distortion.</i>"
        )
    except ValueError:
        await message.answer("Send a valid non-negative number, for example /level 10.")


@router.message(Command("leave"))
async def leave_command(message: Message, db: Database):
    runtime = get_runtime(message.from_user.id)
    if runtime is None:
        await message.answer("No hosted account found.")
        return
    await runtime.stop_bridge()
    await db.clear_bridge(message.from_user.id)
    await message.answer("⏹ Bridge disconnected. Hosted account is still active.")


@router.message(Command("status"))
async def status_command(message: Message):
    await message.answer(status_text(get_runtime(message.from_user.id)))


@router.message(Command("unhost"))
async def unhost_command(message: Message, db: Database):
    user_id = message.from_user.id
    runtime = runtimes.pop(user_id, None)
    pending = pending_logins.pop(user_id, None)
    if pending:
        try:
            await pending["client"].disconnect()
        except Exception:
            pass
    if runtime is not None:
        await runtime.stop()
    await db.delete_account(user_id)
    await message.answer("Hosted account disconnected and its saved MongoDB session removed.")


async def restore_accounts(db: Database, settings: Settings):
    for doc in await db.all_accounts():
        user_id = int(doc["user_id"])
        try:
            runtime = VoiceRuntime(user_id, doc["session"], settings)
            runtime.level = float(doc.get("level", 1.0))
            runtime.dsp = AudioDSP(settings=DSPSettings.from_dict(doc.get("dsp", {})))
            await runtime.start()
            runtimes[user_id] = runtime
            bridge = doc.get("bridge")
            if bridge:
                try:
                    await runtime.start_bridge(
                        int(bridge["source_id"]), int(bridge["target_id"])
                    )
                except Exception:
                    log.exception("Bridge restore failed for user %s", user_id)
                    await db.clear_bridge(user_id)
            log.info("Restored hosted account %s", user_id)
        except Exception:
            log.exception("Could not restore hosted account %s", user_id)


async def shutdown_accounts():
    for user_id, runtime in list(runtimes.items()):
        try:
            await runtime.stop()
        except Exception:
            log.exception("Shutdown failed for %s", user_id)
    runtimes.clear()
