import asyncio
import os
import sys
import logging
import traceback
from datetime import datetime
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from openai import OpenAI
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
# CONFIGURATION / TESTING SETTINGS
# ======================================================
ai_client = OpenAI(
    api_key=settings.openrouter_api_key, base_url="https://openrouter.ai/api/v1"
)

CACHE_FILE = "daily_cache.txt"
EMAIL_HOUR = 8
FETCH_MINUTES = 60
TEST_MODE = True
MODEL_NAME = "openrouter/free"
# ======================================================


async def run_daily_digest():
    now = datetime.now()
    logger.info(
        f"🤖 Agent waking up at {now.strftime('%H:%M:%S')} (TEST_MODE={TEST_MODE}, FETCH_MINUTES={FETCH_MINUTES})..."
    )

    try:
        # Prevent infinite cache growth during testing
        if TEST_MODE and os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)
            logger.info("🗑️ TEST_MODE active: Cleared old cache before starting.")

        transport = StdioTransport(command=sys.executable, args=["-m", "src.server"])

        async with Client(transport) as mcp_client:
            # ==========================================
            # STEP 1: FETCH ONLY THE LAST X MINUTES
            # ==========================================
            channels = [
                c.strip() for c in settings.target_channels.split(",") if c.strip()
            ]
            recent_texts = []

            for channel in channels:
                logger.info(
                    f"📥 Fetching updates for {channel} (last {FETCH_MINUTES} mins)..."
                )
                messages_result = await mcp_client.call_tool(
                    "fetch_channel_updates",
                    {"channel_username": channel, "minutes": FETCH_MINUTES},
                )
                raw_text = str(messages_result)
                if "No text messages found" not in raw_text and "Error" not in raw_text:
                    recent_texts.append(f"=== {channel} ===\n{raw_text}")
                else:
                    logger.info(
                        f"Skipping {channel}: No messages in last {FETCH_MINUTES} mins."
                    )

            # ==========================================
            # STEP 2: HOURLY DIGEST (MAP) & SAVE
            # ==========================================
            if recent_texts:
                logger.info("🧠 Creating hourly mini-digest...")
                combined_hourly = "\n\n".join(recent_texts)

                hourly_prompt = f"""
                Briefly summarize these Telegram messages from the last hour. 
                Discard spam, ads, and junk. KEEP the [ID: link] and [media attach] tags intact so the daily summarizer has them.
                
                MESSAGES:
                {combined_hourly}
                """

                hourly_res = ai_client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=[{"role": "user", "content": hourly_prompt}],
                )

                with open(CACHE_FILE, "a", encoding="utf-8") as f:
                    f.write(f"\n\n--- HOUR: {now.strftime('%H:%M')} ---\n")
                    f.write(hourly_res.choices[0].message.content)
                logger.info("✅ Hourly digest saved to cache.")
            else:
                logger.info("💤 No new messages in this interval.")

            # ==========================================
            # STEP 3: AT 8 AM, DO THE MASTER DIGEST (REDUCE)
            # ==========================================
            should_send = TEST_MODE or (now.hour == EMAIL_HOUR)

            if should_send:
                logger.info("⏰ Triggering summary generation and email delivery...")

                if not os.path.exists(CACHE_FILE):
                    logger.warning("No cache found. Nothing to summarize.")
                    return

                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    full_data = f.read()

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
                                        [Insert the dynamically translated or original text here]
                                    </div>
                                    
                                    <!-- Tags and links ALWAYS stay on the left (LTR) -->
                                    <div style="margin-top: 12px; direction: ltr; text-align: left;">
                                        <span style="display: inline-block; background: #21262d; border: 1px solid #30363d; color: #8b949e; font-size: 12px; padding: 2px 8px; border-radius: 12px; margin-right: 10px;">📎 Media attached</span>
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

                response = ai_client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=[{"role": "user", "content": master_prompt}],
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

                if not TEST_MODE:
                    os.remove(CACHE_FILE)
                    logger.info("🗑️ Production run complete. Cache cleared.")
                else:
                    logger.info(
                        "🧪 Test run complete! Cache preserved in daily_cache.txt for review."
                    )

    except Exception as e:
        # ==========================================
        # CRITICAL ERROR HANDLER
        # ==========================================
        error_details = traceback.format_exc()
        logger.error(f"❌ CRITICAL AGENT CRASH:\n{error_details}")

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
