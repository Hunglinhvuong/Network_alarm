import unittest
from html.parser import HTMLParser
from unittest.mock import patch

from alerting.outbox import _retry_policy
from alerting.pagination import paginate_message, telegram_text_length


class _TagBalanceParser(HTMLParser):
    VOID_TAGS = {"br", "hr", "img"}

    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID_TAGS:
            self.tags.append(tag)

    def handle_endtag(self, tag):
        if not self.tags or self.tags[-1] != tag:
            raise AssertionError(f"Unbalanced closing tag: {tag}")
        self.tags.pop()


class PaginationTests(unittest.TestCase):
    def test_long_html_message_splits_under_telegram_limit_and_balances_tags(self):
        message = "<b>Trạm 🚨 &amp; thiết bị</b> " * 500

        pages = paginate_message(message)

        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertLessEqual(telegram_text_length(page), 4000)
            parser = _TagBalanceParser()
            parser.feed(page)
            self.assertEqual(parser.tags, [])
        self.assertTrue(pages[0].startswith("<b>[1/"))
        self.assertIn("[2/", pages[1])

    def test_short_message_is_returned_unchanged(self):
        message = "<b>Trạm ổn định</b>"

        self.assertEqual(paginate_message(message), [message])


class RetryPolicyTests(unittest.TestCase):
    def test_rate_limit_uses_telegram_retry_after_and_pauses_bot(self):
        with patch("alerting.outbox.random.uniform", return_value=0.5):
            delay, pause_all = _retry_policy(
                429, {"parameters": {"retry_after": 38}}, attempts=1
            )

        self.assertEqual(delay, 38.5)
        self.assertTrue(pause_all)

    def test_server_error_retries_with_backoff_without_global_pause(self):
        with patch("alerting.outbox.random.uniform", return_value=0.25):
            delay, pause_all = _retry_policy(503, {}, attempts=3)

        self.assertEqual(delay, 8.25)
        self.assertFalse(pause_all)

    def test_client_error_is_not_retried(self):
        self.assertEqual(_retry_policy(400, {}, attempts=1), (None, False))


if __name__ == "__main__":
    unittest.main()