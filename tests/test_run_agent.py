import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


os.environ.update(
    TELEGRAM_API_ID="1",
    TELEGRAM_API_HASH="hash",
    EMAIL_SENDER="sender@example.com",
    EMAIL_PASSWORD="password",
    EMAIL_RECEIVER="receiver@example.com",
    OPENROUTER_API_KEY="key",
    TARGET_CHANNELS="channel",
)

import src.run_agent as run_agent
from src.config import settings
from src.run_agent import ensure_email_sent, get_response_content, should_send_digest


class RunAgentTest(unittest.TestCase):
    def test_empty_model_response_is_a_retryable_error(self):
        response = SimpleNamespace(choices=[SimpleNamespace(message=None)])

        with self.assertRaises(RuntimeError):
            get_response_content(response)

    def test_digest_does_not_send_before_delivery_hour(self):
        self.assertFalse(should_send_digest(datetime(2026, 8, 10, 7), 8))

    def test_email_failure_result_is_retryable(self):
        with self.assertRaises(RuntimeError):
            ensure_email_sent("SMTP Error details: connection failed")

    def test_dry_run_writes_preview_without_sending_or_clearing_cache(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as directory:
                cache_file = Path(directory) / "daily_cache.json"
                preview_file = Path(directory) / "digest_preview.html"
                delivery_file = Path(directory) / "delivery_state.json"
                cache_file.write_text(
                    '[{"timestamp":"2026-08-11T08:00:00","time_label":"08:00","content":"news"}]',
                    encoding="utf-8",
                )
                mcp_client = SimpleNamespace(call_tool=AsyncMock())
                response = SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="<html>preview</html>"))]
                )

                with (
                    patch.object(run_agent, "CACHE_FILE", cache_file),
                    patch.object(run_agent, "DRY_RUN_OUTPUT", preview_file),
                    patch.object(run_agent, "DELIVERY_STATE_FILE", delivery_file),
                    patch.object(settings, "dry_run", True),
                    patch.object(
                        run_agent.ai_client.chat.completions,
                        "create",
                        AsyncMock(return_value=response),
                    ),
                ):
                    await run_agent.process_master_digest(
                        mcp_client, datetime(2026, 8, 11, 8)
                    )

                self.assertEqual(preview_file.read_text(), "<html>preview</html>")
                self.assertTrue(cache_file.exists())
                self.assertFalse(delivery_file.exists())
                mcp_client.call_tool.assert_not_awaited()

        import asyncio

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
