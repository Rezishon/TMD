import asyncio
import os
import sys
import json
import logging
import traceback
from datetime import datetime
from typing import List

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from openai import AsyncOpenAI

from src.config import settings
from src.services.email import EmailService

# ======================================================
# LOGGING SETUP
# ======================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("agent.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ======================================================
# CONFIGURATION SETTINGS
# ======================================================
ai_client = AsyncOpenAI(
    api_key=settings.openrouter_api_key, base_url="https://openrouter.ai/api/v1"
)

CACHE_FILE = "daily_cache.json"
LAST_RUN_FILE = "last_run.txt"
RETRY_FILE = "digest_retry.txt"
EMAIL_HOUR = 8
TEST_MODE = True
MODEL_NAME = "nvidia/nemotron-3-ultra-550b-a55b:free"
MAX_CATCHUP_MINUTES = 1440

# ======================================================
# UTILITY AND CACHE FUNCTIONS
# ======================================================


def get_dynamic_fetch_minutes(now: datetime) -> int:
    """Calculates how many minutes to fetch based on the last successful run."""
    if os.path.exists(LAST_RUN_FILE):
        try:
            with open(LAST_RUN_FILE, "r") as f:
                last_run_time = datetime.fromisoformat(f.read().strip())

            delta_minutes = int((now - last_run_time).total_seconds() / 60) + 5
            fetch_mins = min(delta_minutes, MAX_CATCHUP_MINUTES)
            logger.info(
                f"⏱️ Found last run at {last_run_time.strftime('%H:%M')}. Auto-fetching {fetch_mins} minutes."
            )
            return fetch_mins
        except Exception as e:
            logger.warning(
                f"⚠️ Could not read last_run.txt ({e}). Defaulting to 65 mins."
            )
            return 65
    logger.info("🆕 No last_run.txt found. Defaulting to 65 mins.")
    return 65


def append_to_json_cache(now: datetime, content: str) -> None:
    """Safely appends hourly summaries to a structured JSON file."""
    cache_data = []
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cache_data = json.load(f)
        except json.JSONDecodeError:
            logger.warning(
                "⚠️ Cache file corrupted or empty. Starting fresh JSON array."
            )

    cache_data.append(
        {
            "timestamp": now.isoformat(),
            "time_label": now.strftime("%H:%M"),
            "content": content,
        }
    )

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, indent=4, ensure_ascii=False)
    logger.info("✅ Hourly digest safely saved to JSON cache.")


def read_and_format_cache() -> str:
    """Reads JSON cache and returns a formatted string for the LLM."""
    if not os.path.exists(CACHE_FILE):
        return ""
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache_data = json.load(f)
        return "\n\n".join(
            [
                f"--- HOUR: {item['time_label']} ---\n{item['content']}"
                for item in cache_data
            ]
        )
    except Exception as e:
        logger.error(f"❌ Failed to read JSON cache: {e}")
        return ""


def ensure_email_sent(result: str) -> None:
    """STRICT VALIDATION: Ensures email success before cache deletion proceeds."""
    if not result or "Email sent successfully" not in str(result):
        raise RuntimeError(f"Email delivery failed: {result}")


# ======================================================
# CORE BUSINESS LOGIC
# ======================================================


async def process_hourly_fetch(
    mcp_client: Client, fetch_minutes: int, now: datetime
) -> None:
    """Handles fetching Telegram messages and summarizing them hourly."""
    channels = [c.strip() for c in settings.target_channels.split(",") if c.strip()]
    recent_texts: List[str] = []

    for channel in channels:
        logger.info(f"📥 Fetching updates for {channel} (last {fetch_minutes} mins)...")
        try:
            messages_result = await mcp_client.call_tool(
                "fetch_channel_updates",
                {"channel_username": channel, "minutes": fetch_minutes},
            )
            raw_text = str(messages_result)
            if "No text messages found" not in raw_text and "Error" not in raw_text:
                recent_texts.append(f"=== {channel} ===\n{raw_text}")
            else:
                logger.info(f"Skipping {channel}: No messages.")
        except Exception as e:
            logger.error(f"❌ Failed to fetch {channel}: {e}")

    if not recent_texts:
        logger.info("💤 No new messages in this interval.")
        return

    logger.info("🧠 Creating hourly mini-digest...")
    combined_hourly = "\n\n".join(recent_texts)

    hourly_prompt = f"""
    Briefly summarize these Telegram messages. 
    Summarize briefly, preserve each message’s original language, keep the main idea and remove spam.
    Discard spam, ads, and junk entirely. 
    CRITICAL: You MUST keep the exact [ID: https://...] and [media attach] tags attached to their respective summaries. Do not mix them up.
    
    MESSAGES:
    {combined_hourly}
    """

    try:
        # PERFORMANCE: Await the async client network call
        hourly_res = await ai_client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": hourly_prompt}],
        )

        if (
            hourly_res.choices
            and len(hourly_res.choices) > 0
            and hourly_res.choices[0].message
            and hourly_res.choices[0].message.content
        ):
            content = hourly_res.choices[0].message.content.strip()
            append_to_json_cache(now, content)
        else:
            logger.warning("⚠️ OpenRouter returned empty content. Skipping cache write.")
    except Exception as api_err:
        logger.warning(
            f"⚠️ OpenRouter API Error during hourly summary: {api_err}. Skipping cache for this hour."
        )


async def process_master_digest(mcp_client: Client) -> None:
    """Handles reading the cache, generating the master HTML digest, and emailing it."""
    logger.info("⏰ Triggering summary generation and email delivery...")

    full_data = read_and_format_cache()
    if not full_data:
        logger.warning("No cache found or cache is empty. Nothing to summarize.")
        return

    logger.info("🧠 Summarizing cached data into HTML with OpenRouter...")

    master_prompt = f"""
    You are an expert Information Curator and Technical Assistant for a DevOps and Software Engineer. 
    Your job is to process these Telegram messages and generate a highly readable HTML email digest.

    STRICT FILTERING RULES:
    1. NO ads, sponsorships, or promotional material.
    2. NO VIP group promotions, paid courses, or crypto spam.
    3. If a message is purely political or meaningless chatter, ignore it entirely.

    CATEGORIES TO EXTRACT:
    - Programming
    - DevOps, Network & Infrastructure
    - Security
    - Operating Systems & Devices
    - Sports
    - Creative Projects, Fun & Interesting Things
    - News (Tech/Science/Major global events)

    EXTRACTION RULES:
    1. Pick ONLY the top 3 to 5 most important messages per category. 
    2. Overflow: If you find more than 5 items, list the extras at the bottom as brief keywords.
    3. CRITICAL LINK RULE: Convert [ID: https://...] tags into clickable HTML links: 
       <a href="https://t.me/..." style="color: #58a6ff; text-decoration: none; font-weight: bold; font-size: 13px;">View Post &rarr;</a>
    
    OUTPUT FORMAT (STRICT HTML):
    - Output ONLY valid, clean HTML. Do NOT use Markdown fences (no ```html).
    - IMPORTANT RTL RULE: Email clients ignore auto-direction. If you write a summary in Persian (Farsi), you MUST format its container with dir="rtl" and text-align: right. If it is in English, use dir="ltr" and text-align: left.
    
    - Structure your HTML exactly like this:
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            /* Double braces are required here for Python f-string compatibility */
            @media screen and (max-width: 600px) {{
                .main-container {{ padding: 10px !important; }}
                .card {{ padding: 16px !important; }}
            }}
        </style>
    </head>
    <body style="font-family: Tahoma, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; margin: 0; padding: 20px; background-color: #0d1117; color: #c9d1d9; -webkit-font-smoothing: antialiased;">
        
        <div class="main-container" style="max-width: 600px; margin: auto; background-color: #0d1117;">
            
            <h2 style="color: #e6edf3; font-size: 24px; border-bottom: 2px solid #58a6ff; padding-bottom: 10px; margin-bottom: 24px; text-align: left; direction: ltr;">
                <span style="color: #58a6ff;">🚀 Tech</span> & News Digest
            </h2>
            
            <div class="card" style="background: #161b22; padding: 20px; margin-bottom: 20px; border-radius: 12px; border-left: 4px solid #8957e5; box-shadow: 0 4px 12px rgba(0,0,0,0.5);">
                <h3 style="color: #8957e5; margin-top: 0; margin-bottom: 15px; font-size: 16px; text-transform: uppercase; letter-spacing: 1px; text-align: left; direction: ltr;">[Category Name]</h3>
                
                <ul style="padding-left: 0; margin: 0; list-style: none;">
                    <li style="margin-bottom: 16px; padding-bottom: 16px; border-bottom: 1px solid #30363d;">
                        
                        <!-- Channel name ALWAYS stays on the left (LTR) -->
                        <div style="direction: ltr; text-align: left; margin-bottom: 8px;">
                            <strong style="color: #58a6ff; font-size: 15px;">@Channel_Name:</strong> 
                        </div>
                        
                        <!-- APPLY RTL HERE IF PERSIAN: Use dir="rtl" style="text-align: right;" -->
                        <!-- APPLY LTR HERE IF ENGLISH: Use dir="ltr" style="text-align: left;" -->
                        <div dir="ltr" style="color: #e6edf3; margin-top: 4px; font-size: 15px; line-height: 1.8; text-align: left;">
                            [Insert the summary text here]
                        </div>
                        
                        <!-- Tags and links ALWAYS stay on the left (LTR) -->
                        <div style="margin-top: 12px; direction: ltr; text-align: left;">
                            <span style="display: inline-block; background: #21262d; border: 1px solid #30363d; color: #8b949e; font-size: 12px; padding: 2px 8px; border-radius: 12px; margin-right: 10px;">📎 Media attached</span>
                            <!-- CRITICAL: Inject the raw [https://t.me/](https://t.me/)... link directly into the href below -->
                            <a href="[https://t.me/](https://t.me/)..." style="color: #58a6ff; text-decoration: none; font-weight: bold; font-size: 13px;">View Post &rarr;</a>
                        </div>
                    </li>
                </ul>
                
                <p style="font-size: 13px; color: #8b949e; margin-top: 15px; margin-bottom: 0; text-align: left; direction: ltr;">
                    <em>More:</em> <a href="#" style="color: #58a6ff; text-decoration: none;">Keyword 1</a>, <a href="#" style="color: #58a6ff; text-decoration: none;">Keyword 2</a>
                </p>
            </div>
            
        </div>
    </body>
    </html>

    MESSAGES (24 Hourly Summaries):
    {full_data[:80000]}
    """

    # PERFORMANCE: Await the async client network call
    response = await ai_client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": master_prompt}],
    )

    if not response.choices or not response.choices[0].message.content:
        raise RuntimeError(
            "OpenRouter returned empty message content for master digest"
        )

    summary = response.choices[0].message.content
    summary = summary.replace("```html", "").replace("```", "").strip()

    logger.info("📧 Sending email digest...")
    result = await mcp_client.call_tool(
        "send_summary_email",
        {
            "subject": "Tech & News Digest (Test Run)"
            if TEST_MODE
            else "Daily Tech & News Digest",
            "content": summary,
        },
    )
    logger.info(f"Result: {result}")

    # DATA FLOW: If ensure_email_sent fails, execution aborts here. Cache is completely safe.
    ensure_email_sent(str(result))

    if not TEST_MODE:
        os.remove(CACHE_FILE)
        logger.info("🗑️ Production run complete. JSON Cache cleared.")
    else:
        logger.info("🧪 Test run complete! JSON Cache preserved for review.")

    if os.path.exists(RETRY_FILE):
        os.remove(RETRY_FILE)
        logger.info("✅ Cleared pending digest retry marker.")


# ======================================================
# MAIN EXECUTION
# ======================================================


async def run_daily_digest() -> None:
    now = datetime.now()
    fetch_minutes = get_dynamic_fetch_minutes(now)

    logger.info(
        f"🤖 Agent waking up at {now.strftime('%H:%M:%S')} (TEST_MODE={TEST_MODE}, FETCH_MINUTES={fetch_minutes})..."
    )

    try:
        if TEST_MODE and os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)
            logger.info("🗑️ TEST_MODE active: Cleared old JSON cache before starting.")

        transport = StdioTransport(command=sys.executable, args=["-m", "src.server"])

        async with Client(transport) as mcp_client:
            await process_hourly_fetch(mcp_client, fetch_minutes, now)

            with open(LAST_RUN_FILE, "w") as f:
                f.write(now.isoformat())

            # Step 3: Check if Master Digest should run
            should_send = (
                TEST_MODE or now.hour == EMAIL_HOUR or os.path.exists(RETRY_FILE)
            )
            if should_send:
                await process_master_digest(mcp_client)

    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"❌ CRITICAL AGENT CRASH:\n{error_details}")

        with open(RETRY_FILE, "w") as f:
            f.write(datetime.now().isoformat())
        logger.info("🔁 Digest marked for retry on the next hourly run.")

        if not TEST_MODE:
            logger.info("⚠️ Sending emergency error email via direct fallback...")
            error_html = f"""
            <html>
                <body style="font-family: monospace; background-color: #ffe6e6; padding: 20px;">
                    <h2 style="color: #c0392b;">🚨 Telegram Agent Crash Alert</h2>
                    <p>Your VPS agent encountered a critical error at {now.strftime("%H:%M:%S")}.</p>
                    <div style="background: #fff; padding: 15px; border-left: 5px solid #e74c3c; overflow-x: auto;">
                        <pre style="color: #333;">{error_details}</pre>
                    </div>
                </body>
            </html>
            """
            success, msg = EmailService.send_report(
                subject="🚨 Agent Crash Alert", content=error_html
            )
            if success:
                logger.info("✅ Error email sent successfully.")
            else:
                logger.error(f"❌ Failed to send error email: {msg}")


if __name__ == "__main__":
    asyncio.run(run_daily_digest())
