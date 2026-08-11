import asyncio
import fcntl
import sys
import json
import logging
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict, cast

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

CACHE_FILE = Path("daily_cache.json")
LAST_RUN_FILE = Path("last_run.txt")
DELIVERY_STATE_FILE = Path("delivery_state.json")
UNCERTAIN_FILE = Path("uncertain_delivery.json")
EMAIL_HOUR = settings.email_hour
MODEL_NAME = "nvidia/nemotron-3-ultra-550b-a55b:free"
MAX_CATCHUP_MINUTES = 1440
MAX_DIGEST_CHARS = 80_000

# ======================================================
# UTILITY AND CACHE FUNCTIONS
# ======================================================


class CacheEntry(TypedDict):
    timestamp: str
    time_label: str
    content: str


def write_text_atomic(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def load_cache() -> list[CacheEntry]:
    if not CACHE_FILE.exists():
        return []
    data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, list) or any(
        not isinstance(item, dict)
        or not all(isinstance(item.get(key), str) for key in CacheEntry.__annotations__)
        for item in data
    ):
        raise ValueError("Cache must be a JSON list of timestamped text entries")
    return cast(list[CacheEntry], data)


def write_cache(entries: list[CacheEntry], path: Path | None = None) -> None:
    path = path or CACHE_FILE
    if entries:
        write_text_atomic(path, json.dumps(entries, indent=2, ensure_ascii=False))
    else:
        path.unlink(missing_ok=True)


def format_cache(entries: list[CacheEntry]) -> str:
    return "\n\n".join(
        f"--- HOUR: {item['time_label']} ---\n{item['content']}" for item in entries
    )


def load_delivery_state() -> dict[str, Any]:
    try:
        state = json.loads(DELIVERY_STATE_FILE.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def reconcile_delivery() -> None:
    state = load_delivery_state()
    entry_ids = state.get("entry_timestamps", [])
    if not isinstance(entry_ids, list) or not entry_ids:
        return

    entries = load_cache()
    batch = [entry for entry in entries if entry["timestamp"] in entry_ids]
    remaining = [entry for entry in entries if entry["timestamp"] not in entry_ids]
    if state.get("status") == "attempting" and batch:
        quarantine_entries(batch)
        state["status"] = "uncertain"

    write_cache(remaining)
    state["entry_timestamps"] = []
    write_text_atomic(DELIVERY_STATE_FILE, json.dumps(state))


def quarantine_entries(entries: list[CacheEntry]) -> None:
    uncertain: list[CacheEntry] = []
    if UNCERTAIN_FILE.exists():
        uncertain = cast(
            list[CacheEntry], json.loads(UNCERTAIN_FILE.read_text(encoding="utf-8"))
        )
    known = {entry["timestamp"] for entry in uncertain}
    write_cache(
        uncertain + [entry for entry in entries if entry["timestamp"] not in known],
        UNCERTAIN_FILE,
    )


def select_digest_batch(entries: list[CacheEntry]) -> list[CacheEntry]:
    batch: list[CacheEntry] = []
    size = 0
    for entry in entries:
        entry_size = len(format_cache([entry])) + (2 if batch else 0)
        if size + entry_size > MAX_DIGEST_CHARS:
            break
        batch.append(entry)
        size += entry_size
    if entries and not batch:
        raise ValueError("A single cache entry exceeds the digest input limit")
    return batch


def get_dynamic_fetch_minutes(now: datetime) -> int:
    """Calculates how many minutes to fetch based on the last successful run."""
    if LAST_RUN_FILE.exists():
        try:
            last_run_time = datetime.fromisoformat(
                LAST_RUN_FILE.read_text(encoding="utf-8").strip()
            )

            delta_minutes = int((now - last_run_time).total_seconds() / 60) + 5
            fetch_mins = max(1, min(delta_minutes, MAX_CATCHUP_MINUTES))
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
    cache_data = load_cache()
    cache_data.append(
        {
            "timestamp": now.isoformat(),
            "time_label": now.strftime("%H:%M"),
            "content": content,
        }
    )

    write_cache(cache_data)
    logger.info("✅ Hourly digest safely saved to JSON cache.")


def ensure_email_sent(result: str) -> None:
    """STRICT VALIDATION: Ensures email success before cache deletion proceeds."""
    if not result or "Email sent successfully" not in str(result):
        raise RuntimeError(f"Email delivery failed: {result}")


def get_response_content(response: Any) -> str:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as error:
        raise RuntimeError("OpenRouter returned no message content") from error
    if not content or not content.strip():
        raise RuntimeError("OpenRouter returned empty message content")
    return content.strip()


def delivered_today(now: datetime) -> bool:
    return load_delivery_state().get("date") == now.date().isoformat()


def should_send_digest(now: datetime, email_hour: int = EMAIL_HOUR) -> bool:
    return not delivered_today(now) and now.hour >= email_hour


# ======================================================
# CORE BUSINESS LOGIC
# ======================================================


async def process_hourly_fetch(
    mcp_client: Client, fetch_minutes: int, now: datetime
) -> bool:
    """Handles fetching Telegram messages and summarizing them hourly."""
    channels = [c.strip() for c in settings.target_channels.split(",") if c.strip()]
    recent_texts: list[str] = []
    all_channels_fetched = True

    for channel in channels:
        logger.info(f"📥 Fetching updates for {channel} (last {fetch_minutes} mins)...")
        try:
            messages_result = await mcp_client.call_tool(
                "fetch_channel_updates",
                {"channel_username": channel, "minutes": fetch_minutes},
            )
            raw_text = str(messages_result)
            if "No text messages found" not in raw_text:
                recent_texts.append(f"=== {channel} ===\n{raw_text}")
            else:
                logger.info(f"Skipping {channel}: No messages.")
        except Exception as e:
            all_channels_fetched = False
            logger.error(f"❌ Failed to fetch {channel}: {e}")

    if not recent_texts:
        logger.info("💤 No new messages in this interval.")
        return all_channels_fetched
    if not all_channels_fetched:
        logger.warning("Discarding partial fetch; all channels will retry next run.")
        return False

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
            max_tokens=4000,
        )

        append_to_json_cache(now, get_response_content(hourly_res))
        return all_channels_fetched
    except Exception as api_err:
        logger.warning(
            f"⚠️ OpenRouter API Error during hourly summary: {api_err}. Skipping cache for this hour."
        )
        return False


async def process_master_digest(
    mcp_client: Client, now: datetime | None = None
) -> None:
    """Handles reading the cache, generating the master HTML digest, and emailing it."""
    logger.info("⏰ Triggering summary generation and email delivery...")

    cache_entries = load_cache()
    oversized = [
        entry
        for entry in cache_entries
        if len(format_cache([entry])) > MAX_DIGEST_CHARS
    ]
    if oversized:
        quarantine_entries(oversized)
        oversized_ids = {entry["timestamp"] for entry in oversized}
        cache_entries = [
            entry for entry in cache_entries if entry["timestamp"] not in oversized_ids
        ]
        write_cache(cache_entries)
        logger.error("Quarantined %d oversized cache entry(s).", len(oversized))
    batch = select_digest_batch(cache_entries)
    if not batch:
        logger.warning("No cache found or cache is empty. Nothing to summarize.")
        return
    full_data = format_cache(batch)

    now = now or datetime.now()
    recipients = EmailService.validate_config()
    logger.info(
        "✅ Delivery preflight passed: cache readable, SMTP configured, %d recipient(s).",
        len(recipients),
    )

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
                            <a href="https://t.me/..." style="color: #58a6ff; text-decoration: none; font-weight: bold; font-size: 14px;">View Post &rarr;</a>
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
    {full_data}
    """

    # PERFORMANCE: Await the async client network call
    response = await ai_client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": master_prompt}],
    )

    summary = get_response_content(response)
    summary = summary.replace("```html", "").replace("```", "").strip()

    logger.info("📧 Sending email digest...")
    state = {
        "date": now.date().isoformat(),
        "status": "attempting",
        "entry_timestamps": [entry["timestamp"] for entry in batch],
    }
    write_text_atomic(DELIVERY_STATE_FILE, json.dumps(state))
    result = await mcp_client.call_tool(
        "send_summary_email",
        {"subject": "Daily Tech & News Digest", "content": summary},
    )
    result_text = str(result)
    if "NOT_SENT:" in result_text:
        DELIVERY_STATE_FILE.unlink(missing_ok=True)
    ensure_email_sent(result_text)

    logger.info(f"Result: {result}")
    state["status"] = "sent"
    write_text_atomic(DELIVERY_STATE_FILE, json.dumps(state))
    reconcile_delivery()
    logger.info("🗑️ Delivery complete. Sent cache batch cleared.")


# ======================================================
# MAIN EXECUTION
# ======================================================


async def run_daily_digest() -> None:
    now = datetime.now()
    fetch_minutes = get_dynamic_fetch_minutes(now)

    logger.info(
        f"🤖 Agent waking up at {now.strftime('%H:%M:%S')} (FETCH_MINUTES={fetch_minutes})..."
    )

    try:
        reconcile_delivery()
        transport = StdioTransport(command=sys.executable, args=["-m", "src.server"])

        async with Client(transport) as mcp_client:
            fetch_succeeded = await process_hourly_fetch(mcp_client, fetch_minutes, now)
            if fetch_succeeded:
                write_text_atomic(LAST_RUN_FILE, now.isoformat())
            else:
                logger.warning(
                    "Fetch state preserved so failed messages can be retried."
                )

            # Step 3: Check if Master Digest should run
            if should_send_digest(now):
                await process_master_digest(mcp_client, now)

    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"❌ CRITICAL AGENT CRASH:\n{error_details}")

        if not delivered_today(now):
            logger.info("🔁 Delivery remains pending for the next hourly run.")
        else:
            logger.error(
                "Delivery outcome is uncertain; cache preserved and automatic resend disabled."
            )
        raise


if __name__ == "__main__":
    with open(".agent.lock", "w") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            asyncio.run(run_daily_digest())
        except BlockingIOError:
            logger.warning("Another agent run is still active; skipping this run.")
