import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from django.test import SimpleTestCase, override_settings
from openai import APIConnectionError, APIStatusError

from .ai import generate_draft


@override_settings(
    OPENAI_API_KEY="test-key",
    OPENAI_MODEL="gpt-4o-mini",
    AI_TIMEOUT_SECONDS=8,
)
class AIDraftTests(SimpleTestCase):
    def setUp(self):
        client_patch = patch("crewlink.ai.OpenAI")
        self.client_factory = client_patch.start()
        self.addCleanup(client_patch.stop)
        self.completion = self.client_factory.return_value.chat.completions.create

    def fail_with(self, status, code, error_type):
        self.completion.side_effect = APIStatusError(
            "Provider error",
            response=httpx.Response(
                status,
                request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
            ),
            body={"code": code, "type": error_type},
        )

    def test_exhausted_credits_explain_required_action_and_keep_editable_draft(self):
        self.fail_with(429, "credit_balance_exhausted", "insufficient_quota")
        draft = generate_draft("Meeting at the hall at 6pm.")
        self.assertEqual(draft["source"], "fallback:credits_exhausted")
        self.assertIn("credits are exhausted", draft["warning"])
        self.assertIn("add API credits", draft["warning"])
        self.assertEqual(draft["body"], "Meeting at the hall at 6pm.")
        self.assertEqual(draft["status"], "draft")
        self.assertLessEqual(len(draft["push_preview"]), 120)
        self.completion.assert_called_once()

    def test_quota_errors_are_distinguished_from_temporary_rate_limits(self):
        for code in ("insufficient_quota", "project_spend_limit_exceeded", None):
            with self.subTest(code=code):
                self.fail_with(429, code, "insufficient_quota")
                draft = generate_draft("Meeting tonight")
                self.assertEqual(draft["source"], "fallback:quota_exhausted")
                self.assertIn("check API credits and limits", draft["warning"])
                self.assertNotIn("try again shortly", draft["warning"])

    def test_temporary_rate_limit_does_not_claim_credits_are_exhausted(self):
        self.fail_with(429, "rate_limit_exceeded", "rate_limit_error")
        draft = generate_draft("Meeting tonight")
        self.assertEqual(draft["source"], "fallback:rate_limited")
        self.assertIn("try again shortly", draft["warning"])
        self.assertNotIn("credits", draft["warning"])

    def test_server_error_keeps_generic_fallback(self):
        self.fail_with(503, "server_is_overloaded", "service_unavailable_error")
        draft = generate_draft("Meeting tonight")
        self.assertEqual(draft["source"], "fallback:provider_unavailable")

    def test_connection_error_logs_no_credentials_or_raw_provider_message(self):
        self.completion.side_effect = APIConnectionError(
            message="Connection failed with secret test-key",
            request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
        )
        with self.assertLogs("crewlink.ai", level="WARNING") as logs:
            draft = generate_draft("Meeting tonight")
        self.assertEqual(draft["source"], "fallback:provider_unavailable")
        self.assertNotIn("test-key", " ".join(logs.output))

    def test_successful_provider_output_remains_a_draft(self):
        self.completion.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                "title": "Meeting tonight",
                "body": "Please attend the meeting tonight.",
                "push_preview": "Meeting tonight",
            })))],
        )
        draft = generate_draft("Meeting tonight")
        self.assertEqual(draft["source"], "openai")
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["title"], "Meeting tonight")
        self.assertNotIn("warning", draft)

    @override_settings(OPENAI_API_KEY="")
    def test_missing_key_explains_configuration_and_makes_no_provider_request(self):
        draft = generate_draft("Meeting tonight")
        self.assertEqual(draft["source"], "fallback:no_api_key")
        self.assertIn("not configured", draft["warning"])
        self.client_factory.assert_not_called()
