# CHIMNEY VC BOT

A Telegram bot and hosted user-account voice-chat bridge. The hosted account receives audio from a source group voice chat and relays it live into a target group voice chat.

## Commands

| Command | Description |
|---|---|
| /start | Premium control panel |
| /help | Full command guide |
| /host | Host a Telegram user account |
| /join <source_id> <target_id> | Start one-way source-to-target relay |
| /level <number> | Set outgoing audio gain; 1 is original, 10 is 10x, 0 mutes |
| /leave | Leave both voice chats; keep account hosted |
| /status | Show account, source, target and gain |
| /unhost | Disconnect and delete the saved session |
| /cancel | Cancel an in-progress login |

The hosted account must be a member of both groups. Both voice chats must be active and accessible to that account. Use the relay only in groups where you have permission to rebroadcast the audio and participants have been informed.

## GitHub Secrets

Add exactly these four repository secrets:

- API_ID — Telegram API ID
- API_HASH — Telegram API Hash
- BOT_TOKEN — BotFather token
- MONGO_DB — MongoDB connection URI (include a database name in the URI if desired)

No phone number or StringSession secret is required in Actions. Each user completes /host privately through the bot.

## Run on GitHub Actions

1. Add the four secrets under Settings → Secrets and variables → Actions.
2. Open Actions → CHIMNEY VC BOT.
3. Select Run workflow.
4. Open the bot on Telegram and send /start.

A push to main that changes Python, dependency or workflow files also starts the workflow.

## Architecture

- aiogram handles the bot UI, commands and inline menu.
- Telethon handles each user's hosted Telegram account and StringSession.
- PyTgCalls / NTgCalls joins the two voice chats and handles live PCM frames.
- NumPy mixes incoming participant frames and applies the user's gain.
- MongoDB persists encrypted StringSessions and bridge settings.
- GitHub Actions installs FFmpeg and runs the bot.

## Session security

StringSessions are encrypted before being written to MongoDB. The encryption key is derived from API_HASH and BOT_TOKEN so no fifth secret is needed. Keep the MongoDB URI and all four GitHub Secrets private. If either API_HASH or BOT_TOKEN is rotated, existing encrypted sessions cannot be decrypted and users must run /host again. Never publish MongoDB backups or session documents.

## Important runtime limitation

GitHub-hosted runners are temporary. This workflow is configured for a maximum of 350 minutes per run; it is not a permanent 24/7 VPS. Start a new workflow run when the runner expires.

## Audio gain

/level 10 applies 10x digital gain to source audio before it is sent to the target VC. No app-level maximum is imposed. PCM output is still bounded by the audio format, so excessive gain can clip and distort sound.

## Development

Python 3.12 is recommended.

    sudo apt-get install ffmpeg
    pip install -r requirements.txt
    export API_ID=...
    export API_HASH=...
    export BOT_TOKEN=...
    export MONGO_DB=...
    python main.py


## Live audio amplifier DSP

The `feature/vc-audio-amplifier` branch adds a streaming stereo PCM DSP stage between source VC frames and the target VC sender. It ports the supplied VoiceAmp APK's digital processing stages into Python: low-shelf bass EQ, high-shelf treble EQ, delayed echo mix, amplification in dB, percentage gain, distortion shaping and the app's soft limiter. Filter and echo state persist across frames.

| Command | Function |
|---|---|
| `/amp on` or `/amp off` | Enable or bypass DSP |
| `/amp 0-200` | Set amplification in dB (APK default: 20 dB) |
| `/gain 0-100` | Output gain; 50 is unity |
| `/bass 0-100` | Bass shelf; 50 is neutral |
| `/treble 0-100` | Treble shelf; 50 is neutral |
| `/echo 0-100` | Echo mix; 0 is off |
| `/distortion 0-100` | Distortion; 0 is off |
| `/dsp` | Display current DSP settings |

Settings are stored in MongoDB per hosted account and restored after restart. Existing `/level` remains as a legacy post-DSP gain control.

**Porting note:** Android microphone capture, AudioTrack playback and Android hardware AcousticEchoCanceler / NoiseSuppressor / AutomaticGainControl are device APIs and are not copied into the server-side VC relay. The portable PCM DSP stages are adapted from the APK's 44.1 kHz mono processing to the relay's 48 kHz stereo frames. The supplied ZIP is decompiled app source, not the original editable Android project.
