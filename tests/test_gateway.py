"""Gateway rules tested with a fake client (no API key or network).
Run:  python -m tests.test_gateway"""
import json
import time

import numpy as np
from google.genai import types

from helmet import config as cfg
from helmet.gemini_gateway import GeminiEvent, GeminiGateway, prepare_frame

FRAME = np.full((480, 640, 3), 90, np.uint8)


def text_resp(s):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part(text=s)]))])


def fc_resp(*calls):
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(
        role="model", parts=[types.Part(function_call=types.FunctionCall(name=n, args=a)) for n, a in calls]))])


class FakeModels:
    def __init__(self, fn):
        self.fn, self.calls = fn, []

    def generate_content(self, model, contents, config):
        self.calls.append((time.monotonic(), contents, config))
        return self.fn(contents, config)


class FakeClient:
    def __init__(self, fn):
        self.models = FakeModels(fn)


def ev(kind="threat", priority=1, **kw):
    return GeminiEvent(kind, frames=[prepare_frame(FRAME, (100, 100, 200, 200))], facts="car LEFT", priority=priority, **kw)


def wait_idle(g, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and (g.in_flight or g.pending is not None):
        time.sleep(0.01)


def setup(**over):
    base = dict(GEMINI_MIN_INTERVAL_S=0.3, GEMINI_CALL_BUDGET=100, GEMINI_MODE="describe",
                GEMINI_FAILS_BEFORE_COOLDOWN=3, GEMINI_COOLDOWN_S=30.0,
                GEMINI_MAX_WAIT_S={"threat": 1.5, "query": 8.0, "scene": 20.0})
    base.update(over)
    for k, v in base.items():
        setattr(cfg, k, v)


def test_request_shape_and_text_parse():
    setup()
    client = FakeClient(lambda c, conf: text_resp("Large truck passing close on your left."))
    g = GeminiGateway(client=client)
    assert g.submit(ev()) == "queued"
    wait_idle(g)
    acts = g.drain()
    _, contents, conf = client.models.calls[0]
    parts = contents[0].parts
    assert parts[0].inline_data.mime_type == "image/jpeg" and len(parts[0].inline_data.data) < 30000
    assert conf.thinking_config is not None and conf.tools is None
    assert acts[0].kind == "speak" and "truck" in acts[0].args["text"].lower()
    print(f"  jpeg {len(parts[0].inline_data.data)} bytes, spoken: {acts[0].args['text']!r}")


def test_min_interval_and_coalescing():
    setup(GEMINI_MIN_INTERVAL_S=0.5)
    client = FakeClient(lambda c, conf: text_resp("ok"))
    g = GeminiGateway(client=client)
    for _ in range(10):                  # burst of 10 threat events in 0.5 s
        g.submit(ev())
        time.sleep(0.05)
    wait_idle(g)
    ts = [c[0] for c in client.models.calls]
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    print(f"  10 events -> {len(ts)} calls, min gap {min(gaps) if gaps else 0:.2f}s, replaced {g.rejected['replaced']}")
    assert len(ts) <= 3 and all(gap >= 0.49 for gap in gaps)


def test_budget_is_hard():
    setup(GEMINI_MIN_INTERVAL_S=0.0, GEMINI_CALL_BUDGET=3)
    client = FakeClient(lambda c, conf: text_resp("ok"))
    g = GeminiGateway(client=client)
    for _ in range(8):
        g.submit(ev())
        wait_idle(g)
    print(f"  budget 3 -> {len(client.models.calls)} calls, rejections {dict(g.rejected)}")
    assert len(client.models.calls) == 3 and g.rejected["budget"] >= 5


def test_user_query_outranks_threat():
    setup(GEMINI_MIN_INTERVAL_S=0.6)
    client = FakeClient(lambda c, conf: text_resp("ok"))
    g = GeminiGateway(client=client)
    g.submit(ev())                                   # goes out immediately
    time.sleep(0.05)
    assert g.submit(ev("query", priority=0, question="what's behind me?")) == "queued"
    assert g.submit(ev()) == "busy"                  # threat can't bump the rider's question
    wait_idle(g)
    kinds = [c[1][0].parts[-1].text.split("\n\n")[1][:20] for c in client.models.calls]
    print(f"  order: {kinds}")
    assert "rider asked" in kinds[1]


def test_stale_pending_event_expires():
    setup(GEMINI_MIN_INTERVAL_S=1.0, GEMINI_MAX_WAIT_S={"threat": 0.2, "query": 8.0, "scene": 20.0})
    client = FakeClient(lambda c, conf: text_resp("ok"))
    g = GeminiGateway(client=client)
    g.submit(ev())
    time.sleep(0.05)
    g.submit(ev())                                   # would have to wait ~1 s; expires at 0.2 s
    wait_idle(g)
    print(f"  calls {len(client.models.calls)}, expired {g.rejected['expired']}")
    assert len(client.models.calls) == 1 and g.rejected["expired"] == 1


def test_circuit_breaker():
    setup(GEMINI_MIN_INTERVAL_S=0.0)

    def boom(c, conf):
        raise ConnectionError("network down")
    g = GeminiGateway(client=FakeClient(boom))
    for _ in range(3):
        g.submit(ev())
        wait_idle(g)
    r = g.submit(ev())
    print(f"  after 3 failures: submit -> {r!r}; status: {g.status}")
    assert r == "circuit open"


def test_thinking_fallback():
    setup(GEMINI_MIN_INTERVAL_S=0.0)
    seen = []

    def picky(c, conf):
        seen.append(conf.thinking_config is not None)
        if conf.thinking_config is not None:
            raise ValueError("400 INVALID_ARGUMENT: thinking_level minimal is not supported")
        return text_resp("Bus behind you, slowing.")
    g = GeminiGateway(client=FakeClient(picky))
    g.submit(ev())
    wait_idle(g)
    acts = g.drain()
    print(f"  thinking config sent: {seen} -> {acts[0].args['text']!r}")
    assert seen == [True, False] and acts


def test_agent_mode_tools():
    setup(GEMINI_MIN_INTERVAL_S=0.0, GEMINI_MODE="agent")
    client = FakeClient(lambda c, conf: fc_resp(
        ("alert_rider_voice", {"message": "Pickup towing a trailer, close on your left"}),
        ("escalate_rear_light", {"level": "danger", "seconds": 4})))
    g = GeminiGateway(client=client)
    g.submit(ev())
    wait_idle(g)
    conf = client.models.calls[0][2]
    acts = [(a.kind, a.args) for a in g.drain()]
    print(f"  tools declared: {[f.name for f in conf.tools[0].function_declarations]}")
    print(f"  actions: {acts}")
    assert conf.tool_config.function_calling_config.mode.value == "ANY"
    assert acts[0][0] == "speak" and acts[1] == ("light", {"level": "danger", "seconds": 4})


def test_scene_json():
    setup(GEMINI_MIN_INTERVAL_S=0.0, GEMINI_MODE="describe")
    client = FakeClient(lambda c, conf: text_resp(json.dumps(
        {"environment": "fast_open_road", "traffic": "light", "summary": "Two-lane rural road, no shoulder"})))
    g = GeminiGateway(client=client)
    g.submit(ev("scene", priority=2))
    wait_idle(g)
    conf = client.models.calls[0][2]
    acts = [(a.kind, a.args) for a in g.drain()]
    print(f"  scene -> {acts}")
    assert conf.response_mime_type == "application/json"
    assert acts[0] == ("profile", {"profile": "fast_open_road", "reason": "fast_open_road, light traffic"})


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            print(name)
            fn()
    print("ALL PASSED")
