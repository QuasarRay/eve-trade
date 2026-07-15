from __future__ import annotations

import unittest

from observability.ci.redaction import redact_mapping, redact_text, safe_argv


class RedactionTests(unittest.TestCase):
    def test_sensitive_environment_values_preserve_presence_only(self) -> None:
        redacted = redact_mapping(
            {
                "SENTRY_DSN": "https://public:secret@sentry.invalid/1",
                "HONEYCOMB_API_KEY": "super-secret",
                "EMPTY_TOKEN": "",
                "SAFE_VALUE": "visible",
            }
        )

        self.assertEqual(redacted["SENTRY_DSN"], "<redacted:present>")
        self.assertEqual(redacted["HONEYCOMB_API_KEY"], "<redacted:present>")
        self.assertEqual(redacted["EMPTY_TOKEN"], "<redacted:empty>")
        self.assertEqual(redacted["SAFE_VALUE"], "visible")

    def test_inline_credentials_and_secret_arguments_are_removed(self) -> None:
        self.assertNotIn("secret", redact_text("DATABASE_URL=postgres://user:secret@db/eve"))
        self.assertEqual(safe_argv(["tool", "--auth-token", "secret"])[-1], "<redacted:present>")


if __name__ == "__main__":
    unittest.main()

