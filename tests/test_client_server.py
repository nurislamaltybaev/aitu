"""Client <-> server split. The client is tested against a fake HTTP transport, the server through FastAPI's
TestClient with the OpenRouter call faked. No network, no real API key, no .env."""
import base64
import io
import json
import os
import types
import unittest
from unittest import mock

import httpx
from fastapi.testclient import TestClient
from PIL import Image

import vision_client
from server import main as server_main
from server import planner


def png_bytes(w=64, h=36) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(b, "PNG")
    return b.getvalue()


SHOT = types.SimpleNamespace(png=png_bytes(), width=64, height=36, scale=1)
PLAN = {"thought_process": "t", "summary": "click it", "requires_approval": False, "memory": "Q1=B",
        "actions": [{"action": "click", "x": 10, "y": 20}],
        "usage": {"requests": 1, "input": 5000, "output": 200, "cost": 0.0007}}


class ClientCase(unittest.TestCase):
    """Runs vision_client.plan() against a scripted server: each reply is a Response or an exception to raise."""

    def setUp(self):
        self.saved_usage = dict(vision_client.USAGE)
        vision_client.USAGE.update(requests=0, input=0, output=0, cost=0.0)
        self.requests = []
        patches = [mock.patch.object(vision_client.time, "sleep"),
                   mock.patch.object(vision_client, "server_url", return_value="https://jarvis.test")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        vision_client.USAGE.update(self.saved_usage)

    def serve(self, *replies):
        replies = list(replies)

        def handler(request):
            self.requests.append(request)
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        client = httpx.Client(base_url="https://jarvis.test", transport=httpx.MockTransport(handler))
        p = mock.patch.object(vision_client, "_http", return_value=client)
        p.start()
        self.addCleanup(p.stop)


class TestClientPlan(ClientCase):
    def test_sends_screenshot_command_model_memory_history(self):
        self.serve(httpx.Response(200, json=PLAN))
        with mock.patch.dict(os.environ, {"JARVIS_MODEL": "openai/gpt-6-luna"}):
            vision_client.plan("do the quiz", SHOT, ["step 1"], "notes")
        body = json.loads(self.requests[0].content)
        self.assertEqual(self.requests[0].url.path, "/api/plan")
        self.assertEqual(base64.b64decode(body["image_base64"]), SHOT.png)
        self.assertEqual((body["prompt"], body["model"], body["memory"], body["history"]),
                         ("do the quiz", "openai/gpt-6-luna", "notes", ["step 1"]))

    def test_returns_validated_plan_and_adds_up_usage(self):
        self.serve(httpx.Response(200, json=PLAN), httpx.Response(200, json=PLAN))
        p = vision_client.plan("x", SHOT)
        vision_client.plan("x", SHOT)
        self.assertEqual((p.memory, p.actions[0].x, p.requires_approval), ("Q1=B", 10, False))
        self.assertEqual(vision_client.USAGE["requests"], 2)
        self.assertAlmostEqual(vision_client.USAGE["cost"], 0.0014)

    def test_server_error_503_is_retried_once(self):
        self.serve(httpx.Response(503, json={"detail": "waking up"}), httpx.Response(200, json=PLAN))
        self.assertEqual(vision_client.plan("x", SHOT).summary, "click it")
        self.assertEqual(len(self.requests), 2)

    def test_connection_failure_gives_a_clear_message(self):
        self.serve(httpx.ConnectError("refused"), httpx.ConnectError("refused"))
        with self.assertRaises(vision_client.ServerError) as e:
            vision_client.plan("x", SHOT)
        self.assertIn("Cannot reach the Jarvis server at https://jarvis.test", str(e.exception))
        self.assertIn("internet connection", str(e.exception))

    def test_timeout_gives_a_clear_message(self):
        self.serve(httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow"))
        with self.assertRaises(vision_client.ServerError) as e:
            vision_client.plan("x", SHOT)
        self.assertIn("did not answer in time", str(e.exception))

    def test_refusal_is_not_retried_and_shows_the_servers_reason(self):
        self.serve(httpx.Response(400, json={"detail": "model 'x' is not allowed on this server"}))
        with self.assertRaises(vision_client.ServerError) as e:
            vision_client.plan("x", SHOT)
        self.assertEqual(len(self.requests), 1)
        self.assertIn("HTTP 400", str(e.exception))
        self.assertIn("not allowed", str(e.exception))

    def test_malformed_plan_from_server_is_rejected(self):
        self.serve(httpx.Response(200, json={"thought_process": "t", "actions": [{"action": "click"}]}))
        with self.assertRaises(ValueError):
            vision_client.plan("x", SHOT)

    def test_token_header_is_sent_when_configured(self):
        vision_client._http.cache_clear()
        with mock.patch.object(vision_client, "CLIENT_TOKEN", "s3cret"), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JARVIS_TOKEN", None)
            client = vision_client._http.__wrapped__()
        vision_client._http.cache_clear()
        self.assertEqual(client.headers.get("X-Jarvis-Token"), "s3cret")


class TestServerAddress(unittest.TestCase):
    def url(self, argv, env=None):
        with mock.patch.object(vision_client.sys, "argv", ["Jarvis_Portable.exe"] + argv), \
             mock.patch.dict(os.environ, env or {}):
            if not env:
                os.environ.pop("JARVIS_SERVER", None)
            return vision_client.server_url(), vision_client.is_configured()

    def test_placeholder_default_counts_as_not_configured(self):
        self.assertEqual(self.url([]), (vision_client.DEFAULT_SERVER, False))

    def test_command_line_wins_over_environment(self):
        self.assertEqual(self.url(["--server", "https://a.example/"], {"JARVIS_SERVER": "https://b.example"}),
                         ("https://a.example", True))
        self.assertEqual(self.url(["--server=https://c.example"]), ("https://c.example", True))

    def test_environment_is_used_without_command_line(self):
        self.assertEqual(self.url([], {"JARVIS_SERVER": "http://127.0.0.1:8000"}), ("http://127.0.0.1:8000", True))


class TestServer(unittest.TestCase):
    def setUp(self):
        self.api = TestClient(server_main.app)
        self.body = {"image_base64": base64.b64encode(png_bytes(1280, 720)).decode(), "prompt": "do the quiz",
                     "model": "openai/gpt-6-luna", "memory": "", "history": []}

    def fake_model(self, reply, cost=0.0007):
        def call(system, user, png, model=None):
            planner.USAGE["requests"] += 1
            planner.USAGE["cost"] += cost
            self.seen = {"system": system, "user": user, "png": png, "model": model}
            if isinstance(reply, Exception):
                raise reply
            return reply
        return mock.patch.object(planner, "_call", side_effect=call)

    def test_health(self):
        self.assertEqual(self.api.get("/health").json(), {"status": "ok"})

    def test_plan_returns_plan_and_usage(self):
        with self.fake_model(json.dumps({"thought_process": "t", "memory": "m",
                                         "actions": [{"action": "click", "x": 1, "y": 2}]})):
            r = self.api.post("/api/plan", json=self.body)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual((data["memory"], data["actions"][0]["x"], data["usage"]["requests"]), ("m", 1, 1))
        self.assertIn("1280x720", self.seen["system"])  # size read from the PNG header
        self.assertEqual(self.seen["model"], "openai/gpt-6-luna")

    def test_usage_is_per_request(self):
        reply = json.dumps({"thought_process": "t", "actions": [{"action": "press", "key": "tab"}]})
        with self.fake_model(reply):
            self.api.post("/api/plan", json=self.body)
            second = self.api.post("/api/plan", json=self.body).json()
        self.assertEqual(second["usage"]["requests"], 1)

    def test_disallowed_model_is_refused_without_calling_openrouter(self):
        with self.fake_model("{}") as call:
            r = self.api.post("/api/plan", json={**self.body, "model": "openai/gpt-5.5-pro"})
        self.assertEqual(r.status_code, 400)
        call.assert_not_called()

    def test_bad_screenshot_is_refused(self):
        for image in ("not base64 !!", base64.b64encode(b"GIF89a....").decode()):
            r = self.api.post("/api/plan", json={**self.body, "image_base64": image})
            self.assertEqual(r.status_code, 400, image)

    def test_token_is_enforced_when_set(self):
        reply = json.dumps({"thought_process": "t", "done": True})
        with mock.patch.object(server_main, "CLIENT_TOKEN", "s3cret"), self.fake_model(reply):
            self.assertEqual(self.api.post("/api/plan", json=self.body).status_code, 401)
            self.assertEqual(self.api.post("/api/plan", json=self.body, headers={"X-Jarvis-Token": "wrong"}).status_code, 401)
            self.assertEqual(self.api.post("/api/plan", json=self.body, headers={"X-Jarvis-Token": "s3cret"}).status_code, 200)

    def test_provider_failure_becomes_502_with_reason(self):
        with self.fake_model(RuntimeError("402 insufficient credits")):
            r = self.api.post("/api/plan", json=self.body)
        self.assertEqual(r.status_code, 502)
        self.assertIn("insufficient credits", r.json()["detail"])

    def test_invalid_plan_from_model_becomes_502(self):
        with self.fake_model("no json here"):
            r = self.api.post("/api/plan", json=self.body)
        self.assertEqual(r.status_code, 502)
        self.assertIn("valid plan", r.json()["detail"])


class TestOversizedPlan(unittest.TestCase):
    def test_more_than_20_actions_are_cut_to_15(self):
        raw = json.dumps({"thought_process": "t", "actions": [{"action": "press", "key": "tab"}] * 25})
        self.assertEqual(len(planner.parse(raw).actions), 15)

    def test_up_to_20_actions_are_kept(self):
        raw = json.dumps({"thought_process": "t", "actions": [{"action": "press", "key": "tab"}] * 20})
        self.assertEqual(len(planner.parse(raw).actions), 20)

    def test_bare_list_is_cut_too(self):
        self.assertEqual(len(planner.parse(json.dumps([{"action": "press", "key": "tab"}] * 30)).actions), 15)


if __name__ == "__main__":
    unittest.main()
