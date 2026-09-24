# Telegram AI Bot

Telegram bot powered by **Google Gemini AI** — deployable to Render (no PC needed).

## Features
- 🤖 AI-powered answers via Gemini 2.0 Flash
- 💬 Maintains per-user conversation history
- 📊 Trading & crypto focused system prompt
- 🚀 Webhook-based (works on Render free tier)

## Deploy to Render

### Step 1 — Push to GitHub
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USERNAME/tg-ai-bot.git
git push -u origin main
```

### Step 2 — Deploy on Render
1. Go to [render.com](https://render.com) → New → Web Service
2. Connect your GitHub repo
3. Render auto-detects `render.yaml` — just click **Deploy**

### Step 3 — Register Webhook (ONCE after deploy)
After Render gives you a URL (e.g. `https://tg-ai-bot.onrender.com`), open in browser:
```
https://tg-ai-bot.onrender.com/set-webhook?url=https://tg-ai-bot.onrender.com/webhook
```
You should see: `{"ok": true}`

### Done! 🎉
Open Telegram → your bot → ask anything!

## Commands
| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/help` | List commands |
| `/clear` | Reset conversation history |
| `/ping` | Check if bot is alive |

## Environment Variables (already in render.yaml)
| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | From @BotFather |
| `GEMINI_API_KEY` | From Google AI Studio |
| `TELEGRAM_CHAT_ID` | Your Telegram Chat ID |
