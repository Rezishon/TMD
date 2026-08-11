from fastmcp import FastMCP
from src.services.telegram import TelegramService
from src.services.email import EmailService

# Initialize the MCP Server
mcp = FastMCP("TelegramAgent")
telegram_service = TelegramService()


@mcp.tool()
async def list_telegram_channels(limit: int = 15) -> str:
    """
    Lists the Telegram channels the user is currently subscribed to.
    Use this to find channel usernames if the user doesn't specify which ones to read.
    """
    try:
        channels = await telegram_service.list_subscribed_channels(limit)
        return (
            "Subscribed Channels:\n" + "\n".join(channels)
            if channels
            else "No channels found."
        )
    except Exception as e:
        return f"Error fetching channels: {str(e)}"


@mcp.tool()
async def fetch_channel_updates(channel_username: str, minutes: int = 60) -> str:
    """
    Fetches the text messages from a Telegram channel for the past X minutes.
    Use this to read the news or updates from the user's subscribed channels so you can summarize them.
    """
    messages = await telegram_service.get_recent_messages(channel_username, minutes)
    if not messages:
        return f"No text messages found in {channel_username} over the last {minutes} minutes."
    return "\n\n---\n\n".join(messages)


@mcp.tool()
def send_summary_email(subject: str, content: str) -> str:
    """
    Emails the provided summary content to the user.
    Use this after fetching and summarizing channel updates to deliver the final report.
    """
    success, message = EmailService.send_report(subject, content)
    return message


if __name__ == "__main__":
    mcp.run()
