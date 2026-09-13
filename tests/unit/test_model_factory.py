import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app


def live_client(settings, handler, payload):
    app = create_app(settings.model_copy(update={"run_live_llm_tests": True}), transport=httpx.MockTransport(handler))
    return TestClient(app, base_url="http://127.0.0.1")


def configure(client, payload):
    token = client.get("/api/system/session", headers={"X-FinSight-Client": "web"}).json()["token"]
    client.headers["X-FinSight-Session"] = token
    assert client.post("/api/models", json=payload).status_code == 201


def completion(message):
    return {"id": "test-response", "object": "chat.completion", "created": 1, "model": "offline-model",
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}]}


def test_probes_disabled_by_default(client, payload):
    client.post("/api/models", json=payload)
    result = client.post("/api/models/test", json={"mode": "chat"})
    assert result.status_code == 409
    assert result.json()["error"]["category"] == "live_tests_disabled"


def test_model_factory_uses_active_configuration_and_validates_tools(settings, payload):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == f'Bearer {payload["api_key"]}'
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        body = json.loads(request.content)
        assert body["model"] == payload["model"]
        assert body["max_tokens"] == 256
        assert payload["api_key"] not in request.content.decode()
        message = {"role": "assistant", "content": "OK"}
        if "tools" in body:
            message = {"role": "assistant", "content": None, "tool_calls": [
                {"id": "probe", "type": "function", "function": {"name": "connection_probe", "arguments": '{"value":"ok"}'}}]}
        return httpx.Response(200, json=completion(message))

    with live_client(settings, handler, payload) as client:
        configure(client, payload)
        for mode in ("connectivity", "chat", "tools"):
            result = client.post("/api/models/test", json={"mode": mode})
            assert result.status_code == 200, result.text
            assert result.json()["supported"]
    assert len(calls) == 3


@pytest.mark.parametrize(("status", "category"), [(401, "model_auth"), (429, "model_rate_limited"), (500, "model_upstream"), (400, "model_incompatible")])
def test_upstream_errors_are_safe_and_not_retried(settings, payload, status, category, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": "upstream-private-key", "request": payload}})

    with live_client(settings, handler, payload) as client:
        configure(client, payload)
        result = client.post("/api/models/test", json={"mode": "chat"})
        assert result.json()["error"]["category"] == category
        assert "upstream-private-key" not in result.text + caplog.text
        assert payload["api_key"] not in result.text + caplog.text
    assert len(calls) == 1


def test_redirect_does_not_forward_key(settings, payload):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://other.example/steal"})

    with live_client(settings, handler, payload) as client:
        configure(client, payload)
        assert client.post("/api/models/test", json={"mode": "connectivity"}).status_code == 502
    assert len(calls) == 1


def test_invalid_tool_arguments_do_not_pass(settings, payload):
    def handler(request):
        return httpx.Response(200, json=completion({"role": "assistant", "content": None, "tool_calls": [{
            "id": "test", "type": "function", "function": {"name": "connection_probe", "arguments": '{"value":"ok", "unexpected":"secret"}'}}]}))
    with live_client(settings, handler, payload) as client:
        configure(client, payload)
        result = client.post("/api/models/test", json={"mode": "tools"})
        assert result.status_code == 502
        assert result.json()["error"]["category"] == "model_invalid_response"


def test_timeout_is_classified(settings, payload):
    def handler(request):
        raise httpx.ReadTimeout("private upstream detail", request=request)
    with live_client(settings, handler, payload) as client:
        configure(client, payload)
        result = client.post("/api/models/test", json={"mode": "chat"})
        assert result.status_code == 504
        assert result.json()["error"]["retryable"]
        assert "private upstream detail" not in result.text



def parse_sse(text):
    import json as jsonlib
    events = []
    for line in text.splitlines():
        if line.startswith('data: '):
            events.append(jsonlib.loads(line[6:]))
    return events


from contextlib import contextmanager


@contextmanager
def stream_client(settings, handler, provider_payload):
    app = create_app(settings.model_copy(update={'run_live_llm_tests': True}), transport=httpx.MockTransport(handler))
    with TestClient(app, base_url='http://127.0.0.1') as client:
        token = client.get('/api/system/session', headers={'X-FinSight-Client': 'web'}).json()['token']
        client.headers['X-FinSight-Session'] = token
        assert client.post('/api/models', json=provider_payload).status_code == 201
        yield client


def test_stream_requires_explicit_confirmation(settings, payload):
    def no_network(request):
        pytest.fail('Unconfirmed requests must not go online')
    with TestClient(create_app(settings, transport=httpx.MockTransport(no_network)), base_url='http://127.0.0.1') as client:
        configure(client, payload)
        result = client.post('/api/models/chat/stream', json={'prompt': 'hello'})
        assert result.status_code == 409
        assert result.json()['error']['category'] == 'live_tests_disabled'


def test_generation_has_no_short_total_limit_and_exceeds_old_char_cap(settings, payload):
    piece = '长' * 300000  # 300000 chars over the retired 262144-char cap; 900KB within the byte budget
    state = {'count': 0}

    async def handler(request):
        # The upstream needs longer than the profile's old total-timeout to finish.
        for _ in range(7):
            await asyncio.sleep(1.0)
            state['count'] += 1
        return httpx.Response(200, json=completion({"role": "assistant", "content": piece}))

    with stream_client(settings, handler, {**payload, 'max_tokens': 40000, 'timeout': 5}) as client:
        result = client.post('/api/models/chat', json={'prompt': 'long', 'confirm_external': True})
        assert result.status_code == 200, result.text
        assert state['count'] == 7
        assert len(result.json()['content']) == 300000
        assert result.json()['truncated'] is False


def test_transfer_overflow_keeps_partial_and_marks_abnormal(settings, payload):
    piece = 'a' * 500000
    total = {'chunks': 0}

    def handler(request):
        async def stream():
            for _ in range(12):
                total['chunks'] += 1
                yield ('data: ' + json.dumps({'choices': [{'index': 0, 'delta': {'content': piece},
                                                          'finish_reason': None}]}) + '\n\n').encode()
            yield ('data: ' + json.dumps({'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]}) + '\n\n').encode()
            yield b'data: [DONE]\n\n'
        return httpx.Response(200, headers={'Content-Type': 'text/event-stream'}, content=stream())

    with stream_client(settings, handler, {**payload, 'max_tokens': 1024}) as client:
        result = client.post('/api/models/chat/stream', json={'prompt': 'overflow', 'confirm_external': True})
        assert result.status_code == 200
        events = parse_sse(result.text)
        assert events[0]['type'] == 'meta' and events[0]['output']['value'] == 1024
        done = events[-1]
        assert done['type'] == 'done' and done['abnormal'] is True
        assert '安全上限' in done['message'] and len(done['content']) > 3000000
        assert done['content'].startswith('a')


def test_stream_events_never_expose_reasoning_text(settings, payload, caplog):
    def handler(request):
        async def stream():
            for text in ['思考', '继续', '收尾']:
                yield ('data: ' + json.dumps({'choices': [{'index': 0, 'delta': {'reasoning_content': text * 100}}]}) + '\n\n').encode()
            yield ('data: ' + json.dumps({'choices': [{'index': 0, 'delta': {'content': '可见回复'}}]}) + '\n\n').encode()
            yield ('data: ' + json.dumps({'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]}) + '\n\n').encode()
            yield b'data: [DONE]\n\n'
        return httpx.Response(200, headers={'Content-Type': 'text/event-stream'}, content=stream())

    with stream_client(settings, handler, payload) as client:
        result = client.post('/api/models/chat/stream', json={'prompt': 'hi', 'confirm_external': True})
        assert result.status_code == 200
        reasoning_events = [event for event in parse_sse(result.text) if event['type'] == 'reasoning']
        assert len(reasoning_events) == 3
        assert all(set(event) == {'type', 'chars'} and event['chars'] > 0 for event in reasoning_events)
        done = parse_sse(result.text)[-1]
        assert done['content'] == '可见回复' and done['abnormal'] is False
        assert '思考' not in result.text and '收尾' not in result.text
        assert '思考' not in caplog.text


def test_missing_finish_is_reported_as_abnormal_partial(settings, payload):
    def handler(request):
        async def stream():
            yield ('data: ' + json.dumps({'choices': [{'index': 0, 'delta': {'content': '半截回复'}}]}) + '\n\n').encode()
            yield b'data: [DONE]\n\n'
        return httpx.Response(200, headers={'Content-Type': 'text/event-stream'}, content=stream())

    with stream_client(settings, handler, payload) as client:
        result = client.post('/api/models/chat/stream', json={'prompt': 'hi', 'confirm_external': True})
        done = parse_sse(result.text)[-1]
        assert done['abnormal'] is True and done['content'] == '半截回复'
        assert '中断' in done['message']


def test_stream_lock_releases_after_completion(settings, payload):
    def handler(request):
        async def stream():
            yield ('data: ' + json.dumps({'choices': [{'index': 0, 'delta': {'content': 'ok'}, 'finish_reason': 'stop'}]}) + '\n\n').encode()
            yield b'data: [DONE]\n\n'
        return httpx.Response(200, headers={'Content-Type': 'text/event-stream'}, content=stream())

    with stream_client(settings, handler, payload) as client:
        for _ in range(2):
            result = client.post('/api/models/chat/stream', json={'prompt': 'hi', 'confirm_external': True})
            assert result.status_code == 200
            assert parse_sse(result.text)[-1]['abnormal'] is False
        assert not client.app.state.model_factory._probe_lock.locked()


def test_tool_call_id_survives_chunked_merge(settings):
    from backend.app.agent.model_factory import ChunkMerger
    merger = ChunkMerger(1024 * 1024)

    class Delta:
        def __init__(self, content=None, tool_calls=None, reasoning=None):
            self.content = content
            self.tool_calls = tool_calls
            self.reasoning_content = reasoning

    class Choice:
        def __init__(self, delta, finish_reason=None):
            self.delta = delta
            self.finish_reason = finish_reason

    class Chunk:
        def __init__(self, choice):
            self.choices = [choice]

    class Call:
        def __init__(self, index=None, id=None, function=None, type=None):
            self.index = index
            self.id = id
            self.function = function
            self.type = type

    class Function:
        def __init__(self, name=None, arguments=None):
            self.name = name
            self.arguments = arguments

    events = merger.add(Chunk(Choice(Delta(tool_calls=[
        Call(index=0, id='call-1', function=Function(name='tool', arguments='{"value":'))]))))
    assert events == []
    merger.add(Chunk(Choice(Delta(tool_calls=[Call(index=0, function=Function(arguments='"ok"}'))]))))
    merger.add(Chunk(Choice(Delta(), finish_reason='tool_calls')))
    calls = merger.calls
    assert calls[0]['id'] == 'call-1'
    assert calls[0]['function']['name'] == 'tool'
    assert calls[0]['function']['arguments'] == '{"value":"ok"}'
