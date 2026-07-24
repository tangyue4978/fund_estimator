from __future__ import annotations

import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from datasources.http_client import get_text


class HttpClientTests(unittest.TestCase):
    @patch("datasources.http_client.paths.file_http_cache", return_value="cache.json")
    @patch("datasources.http_client.paths.ensure_dirs")
    def test_concurrent_expired_cache_refresh_is_coalesced(self, _ensure_dirs, _cache_path) -> None:
        cache: dict = {}

        def read_cache(_path):
            return dict(cache) if cache else None

        def save_cache(_path, payload):
            cache.update(payload)

        def fetch(*_args, **_kwargs):
            time.sleep(0.05)
            return SimpleNamespace(status_code=200, text="fresh payload")

        with (
            patch("datasources.http_client._read_cache", side_effect=read_cache),
            patch("datasources.http_client.save_json", side_effect=save_cache),
            patch("datasources.http_client._write_raw"),
            patch("datasources.http_client._SESSION.get", side_effect=fetch) as network_get,
            ThreadPoolExecutor(max_workers=6) as executor,
        ):
            results = list(
                executor.map(
                    lambda _: get_text(cache_key="coalesced-test", url="https://example.invalid"),
                    range(6),
                )
            )

        self.assertEqual(network_get.call_count, 1)
        self.assertTrue(all(result.ok and result.text == "fresh payload" for result in results))
        self.assertEqual(sum(1 for result in results if not result.from_cache), 1)

    @patch("datasources.http_client.paths.file_http_cache", return_value="cache.json")
    @patch("datasources.http_client.paths.ensure_dirs")
    def test_cache_write_failure_does_not_discard_valid_response(self, _ensure_dirs, _cache_path) -> None:
        response = SimpleNamespace(status_code=200, text="valid payload")
        with (
            patch("datasources.http_client._read_cache", return_value=None),
            patch("datasources.http_client._SESSION.get", return_value=response),
            patch("datasources.http_client.save_json", side_effect=OSError("disk full")),
            patch("datasources.http_client._write_raw", side_effect=OSError("disk full")),
        ):
            result = get_text(cache_key="test", url="https://example.invalid")

        self.assertTrue(result.ok)
        self.assertEqual(result.text, "valid payload")
        self.assertFalse(result.from_cache)

    @patch("datasources.http_client.paths.file_http_cache", return_value="cache.json")
    @patch("datasources.http_client.paths.ensure_dirs")
    def test_network_failure_uses_valid_stale_cache(self, _ensure_dirs, _cache_path) -> None:
        with (
            patch(
                "datasources.http_client._read_cache",
                return_value={"ts": 1, "text": "jsonpgz({});"},
            ),
            patch("datasources.http_client._SESSION.get", side_effect=TimeoutError()),
        ):
            result = get_text(
                cache_key="test",
                url="https://example.invalid",
                ttl_sec=0,
                text_validator=lambda text: text.startswith("jsonpgz("),
            )

        self.assertTrue(result.ok)
        self.assertTrue(result.from_cache)
        self.assertTrue(result.stale)

    @patch("datasources.http_client.paths.file_http_cache", return_value="cache.json")
    @patch("datasources.http_client.paths.ensure_dirs")
    def test_invalid_html_is_not_saved_as_a_success(self, _ensure_dirs, _cache_path) -> None:
        response = SimpleNamespace(status_code=200, text="<html>not found</html>")
        with (
            patch("datasources.http_client._read_cache", return_value=None),
            patch("datasources.http_client._SESSION.get", return_value=response),
            patch("datasources.http_client.save_json") as save_json,
            patch("datasources.http_client._write_raw"),
        ):
            result = get_text(
                cache_key="test",
                url="https://example.invalid",
                text_validator=lambda text: text.startswith("jsonpgz("),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "response_validation_failed")
        save_json.assert_not_called()


if __name__ == "__main__":
    unittest.main()
