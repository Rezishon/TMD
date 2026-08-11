import asyncio
from src.services.telegram import TelegramService


async def authenticate() -> None:
    print("--- Telegram Userbot Initial Authentication ---")
    print("You will be prompted for your phone number and login code.")

    service = TelegramService()
    try:
        await service.client.start()
        print("\n✅ Authentication successful! Session file created.")
        print("You can now run the main agent script.")
    finally:
        await service.disconnect()


if __name__ == "__main__":
    asyncio.run(authenticate())
