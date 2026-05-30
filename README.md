# 📚 Albaheth — بوت البحث الأكاديمي العراقي

A Telegram bot powered by AI that helps researchers find trusted sources from Iraqi universities and academic journals.

## Features

- 🤖 **AI-powered** — Uses any OpenAI-compatible API (Google Gemini, FreeLLMAPI, Groq, etc.)
- 🔄 **Multi-key fallback** — Add multiple API keys; if one is rate-limited, the next is tried automatically
- 📢 **Force Subscribe** — Optional channel subscription gate
- ♾️ **24/7 uptime** — Deployed on Render with health checks
- ⚙️ **Fully configurable** — All settings via environment variables, no code changes needed

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | Token from [@BotFather](https://t.me/BotFather) |
| `AI_API_KEYS` | ✅ | Comma-separated API keys (fallback order) |
| `AI_BASE_URL` | — | OpenAI-compatible base URL (default: Google Gemini) |
| `AI_MODEL` | — | Model name (default: `gemini-2.5-flash`) |
| `FORCE_SUB_CHANNEL` | — | Channel for forced subscription (e.g. `@mychannel`) |
| `BOT_NAME` | — | Bot display name in messages |
| `PORT` | — | Flask port (set automatically by Render) |

## Using FreeLLMAPI

[FreeLLMAPI](https://github.com/tashfeenahmed/freellmapi) aggregates 12+ free LLM providers behind one OpenAI-compatible endpoint.

1. Deploy your FreeLLMAPI instance on Render
2. Add your provider API keys (Gemini, Groq, Mistral, etc.) inside FreeLLMAPI
3. Set `AI_BASE_URL=https://your-freellmapi.onrender.com/v1`
4. Set `AI_API_KEYS=freellmapi-your-token-here`
5. Set `AI_MODEL` to any model supported by your FreeLLMAPI instance

The bot will automatically fail over between providers as configured in FreeLLMAPI.

## Multiple API Keys Example

```
AI_API_KEYS=AIzaSy...key1,AIzaSy...key2,AIzaSy...key3
```

The bot tries `key1` first. If it's rate-limited (429), it moves to `key2`, then `key3`.

## Deploy on Render

### One-click deploy
Click **New Web Service** on Render, connect your GitHub repo, and fill in the environment variables.

### Manual via render.yaml
The included `render.yaml` pre-configures everything. Render will auto-detect it.

### Required settings on Render
- **Runtime**: Docker
- **Health Check Path**: `/health`
- **Plan**: Free tier works for small bots; upgrade for consistent uptime

## Local Development

```bash
cp .env.example .env
# Fill in .env values

pip install -r requirements.txt
python bot.py
```

## Bot Commands

| Command | Description |
|---|---|
| `/start` | Welcome message |
| `/help` | Usage instructions |
| `/reset` | Clear conversation history |

## Architecture

```
Telegram User
     │
     ▼
Telegram Bot (pyTelegramBotAPI)
     │
     ├── Force Subscribe check (optional)
     │
     ▼
AI Router (multi-key fallback)
     │
     ├── Key 1 ──► OpenAI-compatible API
     ├── Key 2 ──► (fallback if key 1 fails)
     └── Key N ──► (fallback chain)
     │
     ▼
Response → User
```

Flask server runs alongside the bot to serve Render health checks (`/health`) and keep the service alive.
