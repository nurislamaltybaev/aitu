"""brave_search / _search: pins the behaviour the brave_search docstring promises. No network, no .env."""
import io
import json
import os
import unittest
import urllib.error
from unittest import mock

from server import planner as vision_client  # web search runs on the server now


def _urlopen(results):
    """Stand-in for urllib.request.urlopen answering with a Brave-shaped JSON body."""
    return mock.patch("urllib.request.urlopen",
                      return_value=io.BytesIO(json.dumps({"web": {"results": results}}).encode()))


@mock.patch.dict(os.environ, {"BRAVE_API_KEY": " test-key \n"})
class TestBraveSearch(unittest.TestCase):
    def test_has_docstring(self):
        self.assertTrue((vision_client.brave_search.__doc__ or "").strip())

    def test_hits_are_numbered_title_url_snippet_without_html(self):
        hits = [{"title": "<strong>Py</strong>thon", "url": "https://python.org", "description": "The <b>official</b> site"},
                {"title": "Docs", "url": "https://docs.python.org", "description": "Reference"}]
        with _urlopen(hits):
            out = vision_client.brave_search("python")
        self.assertEqual(out, "1. Python - https://python.org\n   The official site\n"
                              "2. Docs - https://docs.python.org\n   Reference")

    def test_hit_without_description_gets_empty_snippet(self):
        with _urlopen([{"title": "T", "url": "https://t.example"}]):
            self.assertEqual(vision_client.brave_search("q"), "1. T - https://t.example\n   ")

    def test_empty_results_return_no_results(self):
        with _urlopen([]):
            self.assertEqual(vision_client.brave_search("q"), "no results")

    def test_body_without_web_section_returns_no_results(self):
        with mock.patch("urllib.request.urlopen", return_value=io.BytesIO(b"{}")):
            self.assertEqual(vision_client.brave_search("q"), "no results")

    def test_request_asks_for_5_hits_with_stripped_key_and_15s_timeout(self):
        with _urlopen([]) as urlopen:
            vision_client.brave_search("a b&c")
        req = urlopen.call_args.args[0]
        self.assertIn("q=a+b%26c", req.full_url)
        self.assertIn("count=5", req.full_url)
        self.assertEqual(req.get_header("X-subscription-token"), "test-key")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 15)

    def test_network_error_propagates(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            with self.assertRaises(urllib.error.URLError):
                vision_client.brave_search("q")


class TestSearchWrapper(unittest.TestCase):
    def test_missing_key_raises_in_brave_search_without_touching_network(self):
        with mock.patch.dict(os.environ), mock.patch("urllib.request.urlopen") as urlopen:
            os.environ.pop("BRAVE_API_KEY", None)
            with self.assertRaises(KeyError):
                vision_client.brave_search("q")
        urlopen.assert_not_called()

    def test_missing_key_becomes_search_failed_text(self):
        with mock.patch.dict(os.environ), mock.patch("urllib.request.urlopen") as urlopen:
            os.environ.pop("BRAVE_API_KEY", None)
            with self.assertLogs("jarvis.vision", "WARNING"):
                out = vision_client._search("q")
        self.assertTrue(out.startswith("search failed: "), out)
        self.assertIn("BRAVE_API_KEY", out)
        urlopen.assert_not_called()

    def test_network_error_becomes_search_failed_text(self):
        with mock.patch("server.planner.brave_search", side_effect=TimeoutError("timed out")):
            with self.assertLogs("jarvis.vision", "WARNING"):
                self.assertEqual(vision_client._search("q"), "search failed: timed out")

    def test_success_is_passed_through_unchanged(self):
        with mock.patch("server.planner.brave_search", return_value="1. T - u\n   s") as bs:
            self.assertEqual(vision_client._search("q"), "1. T - u\n   s")
        bs.assert_called_once_with("q")


if __name__ == "__main__":
    unittest.main()
