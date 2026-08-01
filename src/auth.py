import asyncio
from src.services.telegram import TelegramService


async def authenticate():
    print("--- Telegram Userbot Initial Authentication ---")
    print("You will be prompted for your phone number and login code.")

    service = TelegramService()
    await service.client.start()

    print("\n✅ Authentication successful! Session file created.")
    print("You can now run the main agent script.")
    await service.disconnect()


if __name__ == "__main__":
    asyncio.run(authenticate())
