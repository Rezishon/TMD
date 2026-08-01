from datetime import datetime, timedelta, timezone
from telethon import TelegramClient
from src.config import settings


class TelegramService:
    def __init__(self):
        self.client = TelegramClient(
            settings.telegram_session_name,
            settings.telegram_api_id,
            settings.telegram_api_hash,
        )

    async def connect(self):
        if not self.client.is_connected():
            await self.client.start()

    async def disconnect(self):
        if self.client.is_connected():
            await self.client.disconnect()

    async def list_subscribed_channels(self, limit: int = 20) -> list[str]:
        """Returns a list of channels the user is subscribed to."""
        await self.connect()
        channels = []
        async for dialog in self.client.iter_dialogs():
            if dialog.is_channel:
                channels.append(
                    f"{dialog.name} (@{dialog.entity.username or 'Private'})"
                )
                if len(channels) >= limit:
                    break
        return channels

    async def get_recent_messages(
        self, channel_username: str, minutes: int = 60
    ) -> list[str]:
        """Fetches messages with links, media tags, and first-line truncation over the last X minutes."""
        await self.connect()
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        messages = []

        clean_name = channel_username.lstrip("@")

        async for message in self.client.iter_messages(channel_username, limit=100):
            if message.date < cutoff:
                break

            if message.text or message.media:
                msg_text = message.text or message.message or "[Media-only post]"

                # Token Saver: Grab first line or first 150 characters
                first_line = msg_text.split("\n")[0].strip()
                if len(first_line) > 150:
                    first_line = first_line[:150] + "..."
                elif len(msg_text) > len(first_line):
                    first_line += "..."

                msg_link = f"[https://t.me/](https://t.me/){clean_name}/{message.id}"

                media_tag = ""
                if message.photo:
                    media_tag = " [media attach: photo]"
                elif message.video:
                    media_tag = " [media attach: video]"
                elif message.document:
                    media_tag = " [media attach: document]"

                formatted_message = f"[ID: {msg_link}]{media_tag}\n{first_line}"
                messages.append(formatted_message)

        messages.reverse()
        return messages
