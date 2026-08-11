import os
import unittest
from datetime import datetime
from types import SimpleNamespace


os.environ.update(
    TELEGRAM_API_ID="1",
    TELEGRAM_API_HASH="hash",
    EMAIL_SENDER="sender@example.com",
    EMAIL_PASSWORD="password",
    EMAIL_RECEIVER="receiver@example.com",
    OPENROUTER_API_KEY="key",
    TARGET_CHANNELS="channel",
)

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


if __name__ == "__main__":
    unittest.main()
