"""LLM layer: wire-format conversion, config, SDK error translation. No network."""
from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest

from agent import (ImagePart, LlmConfig, LlmRequest, Message, RealLlmAdapter, ToolCall, ToolSpec)
from agent import llm_convert
from agent.exceptions import (InvalidImageError, LlmCapabilityError, LlmConfigError, LlmProviderError,
                              LlmResponseError, LlmTimeoutError)
from agent.utils import extract_json_object

from tests.agent.helpers import make_png

PNG = make_png()
SPEC = ToolSpec(name="analyze_buy_state", description="d",
                parameters={"type": "object", "properties": {"x": {"type": "integer"}}})


def conversation() -> LlmRequest:
    call = ToolCall(id="c1", name="analyze_buy_state", arguments={"x": 1})
    return LlmRequest(tools=[SPEC], response_schema={"type": "object"}, messages=[
        Message.system("SYS"),
        Message.user("看图", [ImagePart.from_bytes(PNG, label="a.png")]),
        Message.assistant("calling", [call, ToolCall(id="c2", name="analyze_buy_state", arguments={"x": 2})]),
        Message.tool_result(call, '{"status":"success"}'),
        Message.tool_result(ToolCall(id="c2", name="analyze_buy_state"), '{"status":"failed"}', is_error=True),
        Message.user("继续")])


# ----------------------------------------------------------------- images
def test_image_part_sniffs_real_format_not_extension(tmp_path):
    f = tmp_path / "shot.jpg"          # lies about being a jpg
    f.write_bytes(PNG)
    assert ImagePart.from_file(f).media_type == "image/png"
    assert base64.b64decode(ImagePart.from_file(f).data) == PNG


@pytest.mark.parametrize("raw,fragment", [(b"", "empty"), (b"%PDF-1.4", "unsupported"), (b"hello", "unsupported")])
def test_bad_images_rejected(raw, fragment):
    with pytest.raises(InvalidImageError, match=fragment):
        ImagePart.from_bytes(raw)


def test_oversized_and_missing_images_rejected(tmp_path):
    with pytest.raises(InvalidImageError, match="limit"):
        ImagePart.from_bytes(PNG, max_bytes=10)
    with pytest.raises(InvalidImageError, match="cannot read"):
        ImagePart.from_file(tmp_path / "missing.png")


# ----------------------------------------------------------------- openai
def test_openai_payload_has_real_image_and_tool_structure():
    p = llm_convert.to_openai(conversation(), model="m", max_tokens=100, json_mode=True)
    roles = [m["role"] for m in p["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "tool", "user"]
    assert "JSON Schema" in p["messages"][0]["content"] and p["messages"][0]["content"].startswith("SYS")
    user = p["messages"][1]["content"]
    assert user[0] == {"type": "text", "text": "看图"}
    assert user[1]["type"] == "image_url" and user[1]["image_url"]["url"].startswith("data:image/png;base64,")
    asst = p["messages"][2]
    assert [c["id"] for c in asst["tool_calls"]] == ["c1", "c2"]
    assert json.loads(asst["tool_calls"][0]["function"]["arguments"]) == {"x": 1}
    assert p["messages"][3]["tool_call_id"] == "c1" and p["messages"][4]["tool_call_id"] == "c2"
    assert p["tools"][0]["function"]["name"] == "analyze_buy_state" and p["tool_choice"] == "auto"
    assert "response_format" not in p                       # JSON mode off while tools are offered
    assert p["max_tokens"] == 100


def test_openai_json_mode_only_on_toolless_turns_and_token_field_is_configurable():
    req = LlmRequest(messages=[Message.user("x")], response_schema={"type": "object"})
    p = llm_convert.to_openai(req, model="m", max_tokens=9, json_mode=True, max_tokens_field="max_completion_tokens")
    assert p["response_format"] == {"type": "json_object"} and p["max_completion_tokens"] == 9 and "max_tokens" not in p
    assert "response_format" not in llm_convert.to_openai(req, model="m", max_tokens=9, json_mode=False)


def test_openai_text_only_user_message_stays_a_plain_string():
    p = llm_convert.to_openai(LlmRequest(messages=[Message.user("hi")]), model="m", max_tokens=1, json_mode=False)
    assert p["messages"][0] == {"role": "user", "content": "hi"}


def test_parse_openai_tool_calls_and_usage():
    data = {"model": "m", "choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
        {"id": "a", "type": "function", "function": {"name": "t", "arguments": '{"k": 1}'}}]}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3}}
    r = llm_convert.parse_openai(data)
    assert r.finish_reason == "tool_calls" and r.tool_calls[0].arguments == {"k": 1}
    assert (r.usage.input_tokens, r.usage.output_tokens) == (7, 3)


def test_parse_openai_text_and_content_parts():
    r = llm_convert.parse_openai({"choices": [{"finish_reason": "length",
                                               "message": {"content": [{"type": "text", "text": "ab"}, {"type": "text", "text": "c"}]}}]})
    assert r.text == "abc" and r.finish_reason == "length"


@pytest.mark.parametrize("args,err", [("{not json", "not valid JSON"), ("[1,2]", "JSON object")])
def test_malformed_tool_arguments_are_kept_for_recovery_not_raised(args, err):
    r = llm_convert.parse_openai({"choices": [{"message": {"tool_calls": [
        {"id": "a", "function": {"name": "t", "arguments": args}}]}}]})
    assert err in r.tool_calls[0].parse_error and r.tool_calls[0].raw_arguments == args
    echoed = llm_convert.to_openai(LlmRequest(messages=[Message.assistant("", r.tool_calls)]), model="m", max_tokens=1, json_mode=False)
    assert echoed["messages"][0]["tool_calls"][0]["function"]["arguments"] == args


def test_empty_arguments_and_missing_ids():
    r = llm_convert.parse_openai({"choices": [{"message": {"tool_calls": [{"function": {"name": "t", "arguments": ""}}]}}]})
    assert r.tool_calls[0].id == "call_0" and r.tool_calls[0].arguments == {}


@pytest.mark.parametrize("data", [{}, {"choices": []}, {"choices": ["x"]}])
def test_parse_openai_rejects_garbage(data):
    with pytest.raises(LlmResponseError):
        llm_convert.parse_openai(data)


# --------------------------------------------------------------- anthropic
def test_anthropic_payload_merges_roles_and_tool_results():
    p = llm_convert.to_anthropic(conversation(), model="m", max_tokens=50)
    assert p["system"].startswith("SYS") and p["max_tokens"] == 50
    assert [m["role"] for m in p["messages"]] == ["user", "assistant", "user"]
    image = p["messages"][0]["content"][1]
    assert image["type"] == "image" and image["source"]["type"] == "base64" and image["source"]["media_type"] == "image/png"
    blocks = p["messages"][1]["content"]
    assert [b["type"] for b in blocks] == ["text", "tool_use", "tool_use"] and blocks[1]["input"] == {"x": 1}
    last = p["messages"][2]["content"]
    assert [b["type"] for b in last] == ["tool_result", "tool_result", "text"]
    assert last[1]["is_error"] is True and last[0]["tool_use_id"] == "c1"
    assert p["tools"][0]["input_schema"] == SPEC.parameters and p["tool_choice"] == {"type": "auto"}


def test_parse_anthropic():
    r = llm_convert.parse_anthropic({"model": "m", "stop_reason": "tool_use", "usage": {"input_tokens": 5, "output_tokens": 2},
                                     "content": [{"type": "text", "text": "hi"},
                                                 {"type": "tool_use", "id": "u1", "name": "t", "input": {"a": 1}}]})
    assert r.text == "hi" and r.tool_calls[0].arguments == {"a": 1} and r.finish_reason == "tool_calls"
    with pytest.raises(LlmResponseError):
        llm_convert.parse_anthropic({"content": "nope"})


# ------------------------------------------------------------------ config
def env(**kw):
    return {f"VALORANT_LLM_{k.upper()}": v for k, v in kw.items()}


def test_config_from_env():
    c = LlmConfig.from_env(env(api_key="k", model="m", base_url="http://x/v1", format="anthropic",
                               timeout_s="30", vision="0", json_mode="1"))
    assert (c.api_key, c.model, c.api_format, c.timeout_s, c.vision, c.json_mode) == ("k", "m", "anthropic", 30.0, False, True)
    assert LlmConfig.from_env(env(api_key="k", model="m")).api_format == "openai"


@pytest.mark.parametrize("e,fragment", [({}, "API_KEY"), (env(api_key="k"), "MODEL"),
                                        (env(api_key="k", model="m", timeout_s="abc"), "invalid"),
                                        (env(api_key="k", model="m", format="grpc"), "invalid")])
def test_config_errors_name_the_problem(e, fragment):
    with pytest.raises(LlmConfigError, match=fragment):
        LlmConfig.from_env(e)


def test_api_key_never_appears_in_repr():
    assert "secret-key" not in repr(LlmConfig(api_key="secret-key", model="m"))


# ----------------------------------------------------------- real adapter
class FakeSdkResponse:
    def __init__(self, data): self._d = data
    def model_dump(self): return self._d


def fake_client(result=None, exc=None, fmt="openai"):
    calls = []

    async def create(**kw):
        calls.append(kw)
        if exc:
            raise exc
        return FakeSdkResponse(result)

    ns = SimpleNamespace(create=create)
    client = SimpleNamespace(chat=SimpleNamespace(completions=ns)) if fmt == "openai" else SimpleNamespace(messages=ns)
    client.calls = calls
    return client


OPENAI_OK = {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}], "usage": {}}


async def test_real_adapter_openai_roundtrip():
    client = fake_client(OPENAI_OK)
    adapter = RealLlmAdapter(LlmConfig(api_key="k", model="gpt", timeout_s=9), client=client)
    resp = await adapter.chat(conversation())
    sent = client.calls[0]
    assert resp.text == "{}" and sent["model"] == "gpt" and sent["timeout"] == 9
    assert any(isinstance(p, dict) and p.get("type") == "image_url" for p in sent["messages"][1]["content"])


async def test_real_adapter_anthropic_roundtrip():
    client = fake_client({"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {}}, fmt="anthropic")
    adapter = RealLlmAdapter(LlmConfig(api_key="k", model="claude", api_format="anthropic"), client=client)
    assert (await adapter.chat(conversation())).text == "ok" and client.calls[0]["model"] == "claude"


async def test_images_refused_when_vision_is_off_and_nothing_is_sent():
    client = fake_client(OPENAI_OK)
    adapter = RealLlmAdapter(LlmConfig(api_key="k", model="m", vision=False), client=client)
    with pytest.raises(LlmCapabilityError, match="vision"):
        await adapter.chat(conversation())
    assert client.calls == []
    await adapter.chat(LlmRequest(messages=[Message.user("text only")]))   # text still fine


class APITimeoutError(Exception): pass
class APIConnectionError(Exception): pass
class APIStatusError(Exception):
    def __init__(self, m, status_code): super().__init__(m); self.status_code = status_code
class RateLimitError(APIStatusError): pass


@pytest.mark.parametrize("exc,kind,status", [(APITimeoutError("t"), LlmTimeoutError, None),
                                             (APIConnectionError("c"), LlmProviderError, None),
                                             (APIStatusError("bad", 500), LlmProviderError, 500),
                                             (RateLimitError("slow down", 429), LlmProviderError, 429),
                                             (TimeoutError("t"), LlmTimeoutError, None)])
async def test_sdk_errors_are_translated(exc, kind, status):
    adapter = RealLlmAdapter(LlmConfig(api_key="k", model="m"), client=fake_client(exc=exc))
    with pytest.raises(kind) as info:
        await adapter.chat(LlmRequest(messages=[Message.user("x")]))
    assert getattr(info.value, "status_code", None) == status


async def test_unexpected_exceptions_are_not_swallowed():
    adapter = RealLlmAdapter(LlmConfig(api_key="k", model="m"), client=fake_client(exc=KeyError("bug")))
    with pytest.raises(KeyError):
        await adapter.chat(LlmRequest(messages=[Message.user("x")]))


async def test_malformed_provider_payload():
    adapter = RealLlmAdapter(LlmConfig(api_key="k", model="m"), client=fake_client({"choices": []}))
    with pytest.raises(LlmResponseError):
        await adapter.chat(LlmRequest(messages=[Message.user("x")]))


def test_sdk_clients_are_constructed_with_timeout_and_retries():
    a = RealLlmAdapter(LlmConfig(api_key="k", model="m", base_url="http://localhost:1/v1", max_retries=1))
    assert type(a._client).__module__.startswith("openai")
    b = RealLlmAdapter(LlmConfig(api_key="k", model="m", api_format="anthropic", base_url="http://localhost:1"))
    assert type(b._client).__module__.startswith("anthropic")


# ------------------------------------------------------------------- utils
@pytest.mark.parametrize("text", ['{"a": 1}', 'sure!\n```json\n{"a": 1}\n```', '```\n{"a": 1}\n```',
                                  'Here: {"a": 1} hope that helps'])
def test_extract_json_object(text):
    assert extract_json_object(text) == {"a": 1}


@pytest.mark.parametrize("text", ["", "no json", "[1,2]", '{"a": '])
def test_extract_json_object_rejects(text):
    assert extract_json_object(text) is None
