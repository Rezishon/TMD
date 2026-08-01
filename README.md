# TMD (Telegram Messages Digester)

An intelligent, highly-optimized AI agent that monitors your favorite Telegram channels, filters out noise (ads, crypto spam), and delivers a beautifully formatted, dark-mode HTML executive summary to your email inbox every morning.

Built with Python, FastMCP, Telethon, and powered by OpenRouter's free tier models, this agent uses a Map-Reduce architecture to process massive amounts of text while bypassing token limits and keeping API costs at exactly zero.

## Key Features

- **Map-Reduce Architecture:** Wakes up every hour to fetch new messages and creates a mini-summary cache, avoiding massive context window limits.
- **Token Optimization (First-Line Truncation):** Prevents context-window overflow by intelligently stripping raw Telegram messages down to their first 150 characters before sending them to the AI.
- **100% Free LLM Integration:** Uses the OpenAI SDK routed through OpenRouter's `openrouter/free` endpoint to automatically select the most capable free model with a massive context window.
- **Dynamic RTL/LTR Support:** The agent dynamically adjusts HTML text alignment. English channels render Left-to-Right, while Persian/Arabic content renders Right-to-Left with proper punctuation.
- **Sleek Dark-Mode UI:** Generates a mobile-responsive, dark-mode HTML email with glowing accent borders, clickable direct Telegram links, and media attachment tags.
- **Multi-Recipient Email:** Supports sending the daily digest to multiple comma-separated email addresses simultaneously.
- **Production Crash Alerts:** Wrapped in a global exception handler. If the agent crashes, it bypasses the MCP server and immediately emails the exact traceback error directly to you.

---

## Prerequisites

Before running this project, you will need:

1. **Python 3.10+** and the **`uv`** package manager.
2. **Telegram API Credentials:** Get your `api_id` and `api_hash` from my.telegram.org.
3. **OpenRouter API Key:** Get a free key from OpenRouter.ai.
4. **Gmail App Password:** If using Gmail, enable 2FA and generate a 16-character App Password for SMTP access.

---

## Installation & Setup

**1. Clone the repository and install dependencies:**

```bash
git clone https://github.com/Rezishon/TMD.git
cd TMD
uv sync
```

**2. Configure your Environment Variables:**
Create a `.env` file in the root directory and add the following keys:

```env
# Telegram Credentials
TELEGRAM_API_ID=1234567
TELEGRAM_API_HASH=your_telegram_api_hash_here

# OpenRouter
OPENROUTER_API_KEY=sk-or-v1-your-key-here

# Email Configuration
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
EMAIL_SENDER=your_email@gmail.com
EMAIL_PASSWORD=your_16_char_app_password
# You can add multiple receivers separated by commas
EMAIL_RECEIVER="your_email@gmail.com, team_member@gmail.com"

# Target Channels (Comma-separated, no @ needed unless preferred)
TARGET_CHANNELS="Linuxor, devtwitter, f1newsir, GitHub"
```

**3. Initial Telegram Authentication:**
Before the background agent can run, you must generate a session file. Run the authentication script:

```bash
uv run python -m src.auth
```

Enter your phone number and the login code sent to your Telegram app. This generates a local `.session` file which allows the agent to read channels continuously.

---

## Technical Guide: Customization (Lines to Change)

You can easily tweak the agent's behavior by modifying a few variables in `src/run_agent.py`:

- **Delivery Time:** Change `EMAIL_HOUR = 8` to whatever hour you want to receive your morning digest (based on your server's timezone).
- **Test Mode:** Set `TEST_MODE = True` to bypass the scheduled hour lock and send an email immediately for debugging. Make sure to set this back to `False` for production.
- **Custom Categories:** Scroll down to the `master_prompt` and change the `CATEGORIES TO EXTRACT:` bullet list to match your exact interests (e.g., change "Sports" to "Finance").
- **Prompt Instructions:** Add strict rules to the `STRICT FILTERING RULES:` section of the `master_prompt` if you notice specific spam or unwanted content slipping through.
- **Model Selection:** The default is `MODEL_NAME = "openrouter/free"`, which automatically routes to a highly capable free model. You can change this to a specific slug (e.g., `"google/gemini-2.0-flash:free"`) if you prefer a static model.

---

## Deployment (Production)

To run this continuously on a VPS (like Ubuntu/Debian), set up a cron job that executes the agent at the top of every hour so it can build its cache.

Open your crontab:

```bash
crontab -e
```

Add the following line (adjusting paths to your specific setup):

```cron
0 * * * * cd /path/to/TMD && /root/.cargo/bin/uv run python -m src.run_agent >> agent.log 2>&1
```

---

## Security Warning

**Never commit your `.env` or `telegram_agent.session` files to version control.** The `.session` file acts as a fully authenticated login to your Telegram account.

Ensure your `.gitignore` looks exactly like this:

```text
.env
*.session
*.session-journal
agent.log
daily_cache.txt
__pycache__/
.venv/
```
