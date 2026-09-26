"""Provider request/response shapes — checked against the Claude Fable 5.1 docs (2026-09-26)."""
from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from _helpers import repo_config

from godotai.providers import ProviderError, ToolCall, make_provider
from godotai.providers.anthropic import API_VERSION, AnthropicProvider
from godotai.providers.openai_compat import OpenAICompatProvider
from godotai.tools import build_registry

TOOLS = build_registry(include_github=False).definitions()
SYSTEM = "You are godotai."


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.calls: list[tuple[str, dict, dict]] = []

    def __call__(self, url, headers, body):
        self.calls.append((url, headers, body))
        return self.response


class AnthropicRequestTests(unittest.TestCase):
    def setUp(self):
        self.p = AnthropicProvider("claude-fable-5-1", api_key="test-key", effort="max", max_tokens=64000)

    def test_request_shape_follows_fable_5_1_rules(self):
        msgs = [self.p.user_message("hello")]
        body = self.p.build_request(SYSTEM, msgs, TOOLS)
        self.assertEqual(body["model"], "claude-fable-5-1")
        self.assertEqual(body["max_tokens"], 64000)
        self.assertEqual(body["tool_choice"], {"type": "auto"}, "forced tool use is a 400 on Fable 5.1")
        self.assertEqual(body["output_config"], {"effort": "max"})
        for forbidden in ("thinking", "temperature", "top_p", "top_k"):
            self.assertNotIn(forbidden, body, forbidden)
        self.assertEqual(body["system"][0]["text"], SYSTEM)
        self.assertEqual(body["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(body["tools"][-1]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(len(body["tools"]), len(TOOLS))
        self.assertEqual({t["name"] for t in body["tools"]}, {t["name"] for t in TOOLS})
        for t in body["tools"]:
            self.assertIn("input_schema", t)
            self.assertNotIn("strict", t)
        json.dumps(body)  # serialisable

    def test_strict_tools_flag(self):
        p = AnthropicProvider("claude-fable-5-1", api_key="k", strict_tools=True)
        body = p.build_request(SYSTEM, [], TOOLS)
        self.assertTrue(all(t.get("strict") is True for t in body["tools"]))

    def test_default_effort_omits_output_config(self):
        p = AnthropicProvider("claude-fable-5-1", api_key="k", effort="default")
        self.assertNotIn("output_config", p.build_request(SYSTEM, [], TOOLS))

    def test_complete_parses_text_and_tool_use_and_keeps_thinking_blocks(self):
        content = [
            {"type": "thinking", "thinking": "…", "signature": "sig"},
            {"type": "text", "text": "Let me look at the project."},
            {"type": "tool_use", "id": "toolu_1", "name": "list_files", "input": {}},
            {"type": "tool_use", "id": "toolu_2", "name": "read_file", "input": {"path": "project.godot"}},
        ]
        tr = FakeTransport({"type": "message", "role": "assistant", "content": content, "stop_reason": "tool_use",
                            "usage": {"input_tokens": 10, "output_tokens": 5}})
        self.p.transport = tr
        turn = self.p.complete(SYSTEM, [self.p.user_message("go")], TOOLS)
        self.assertEqual(turn.text, "Let me look at the project.")
        self.assertEqual([c.name for c in turn.tool_calls], ["list_files", "read_file"])
        self.assertEqual(turn.tool_calls[1].args, {"path": "project.godot"})
        self.assertTrue(turn.wants_tools)
        self.assertFalse(turn.refusal)
        # the whole content (incl. thinking + signature) is replayed verbatim
        self.assertEqual(turn.raw_assistant_message, {"role": "assistant", "content": content})
        url, headers, body = tr.calls[0]
        self.assertEqual(url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(headers["x-api-key"], "test-key")
        self.assertEqual(headers["anthropic-version"], API_VERSION)
        # tool results go back as a single user message with tool_result blocks
        msgs = self.p.tool_results_message([(turn.tool_calls[0], "a.gd", False), (turn.tool_calls[1], "boom", True)])
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["role"], "user")
        self.assertEqual(msgs[0]["content"][0], {"type": "tool_result", "tool_use_id": "toolu_1", "content": "a.gd", "is_error": False})
        self.assertTrue(msgs[0]["content"][1]["is_error"])

    def test_refusal_and_errors(self):
        self.p.transport = FakeTransport({"type": "message", "content": [], "stop_reason": "refusal", "usage": {}})
        self.assertTrue(self.p.complete(SYSTEM, [], TOOLS).refusal)
        self.p.transport = FakeTransport({"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}})
        with self.assertRaises(ProviderError):
            self.p.complete(SYSTEM, [], TOOLS)
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            with self.assertRaises(ProviderError):
                AnthropicProvider("claude-fable-5-1", api_key=None).complete(SYSTEM, [], TOOLS)

    def test_custom_base_url(self):
        p = AnthropicProvider("claude-fable-5-1", api_key="k", base_url="https://proxy.example.com/")
        self.assertEqual(p.url, "https://proxy.example.com/v1/messages")


class OpenAICompatTests(unittest.TestCase):
    def test_request_shape(self):
        p = OpenAICompatProvider("qwen3-coder", api_key="k", base_url="http://localhost:11434/v1", effort="max",
                                 send_reasoning_effort=True)
        body = p.build_request(SYSTEM, [p.user_message("hi")], TOOLS)
        self.assertEqual(p.url, "http://localhost:11434/v1/chat/completions")
        self.assertEqual(body["messages"][0], {"role": "system", "content": SYSTEM})
        self.assertEqual(body["messages"][1], {"role": "user", "content": "hi"})
        self.assertEqual(body["tool_choice"], "auto")
        self.assertEqual(body["reasoning_effort"], "high", "'max' is mapped to the highest OpenAI-style level")
        self.assertEqual(body["tools"][0]["type"], "function")
        self.assertIn("parameters", body["tools"][0]["function"])
        p2 = OpenAICompatProvider("m", api_key="k", send_reasoning_effort=False)
        self.assertNotIn("reasoning_effort", p2.build_request(SYSTEM, [], TOOLS))

    def test_complete_parses_tool_calls(self):
        p = OpenAICompatProvider("m", api_key="k", base_url="http://x/v1")
        p.transport = FakeTransport({"choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "reasoning_content": "thinking…",
            "tool_calls": [{"id": "call_1", "type": "function",
                            "function": {"name": "write_file", "arguments": json.dumps({"path": "a.gd", "content": "x", "step_id": "S1"})}},
                           {"id": "call_2", "type": "function", "function": {"name": "x", "arguments": "{not json"}}]}}],
            "usage": {"prompt_tokens": 1}})
        turn = p.complete(SYSTEM, [], TOOLS)
        self.assertEqual(turn.tool_calls[0].args["step_id"], "S1")
        self.assertIn("_raw", turn.tool_calls[1].args)
        self.assertEqual(turn.raw_assistant_message["reasoning_content"], "thinking…")
        self.assertEqual(len(turn.raw_assistant_message["tool_calls"]), 2)
        msgs = p.tool_results_message([(turn.tool_calls[0], "ok", False), (turn.tool_calls[1], "bad", True)])
        self.assertEqual(msgs[0], {"role": "tool", "tool_call_id": "call_1", "content": "ok"})
        self.assertEqual(msgs[1]["content"], "ERROR: bad")

    def test_error_and_content_parts(self):
        p = OpenAICompatProvider("m", api_key="k", base_url="http://x/v1")
        p.transport = FakeTransport({"error": {"message": "nope"}})
        with self.assertRaises(ProviderError):
            p.complete(SYSTEM, [], TOOLS)
        p.transport = FakeTransport({"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}}]})
        turn = p.complete(SYSTEM, [], TOOLS)
        self.assertEqual(turn.text, "ab")
        self.assertFalse(turn.wants_tools)


class FactoryTests(unittest.TestCase):
    def test_make_provider_from_repo_config(self):
        cfg = repo_config()
        p = make_provider(cfg.agent, api_key="k")
        self.assertIsInstance(p, AnthropicProvider)
        self.assertEqual(p.model, "claude-fable-5-1")
        self.assertEqual(p.effort, "max")
        self.assertEqual(p.max_tokens, cfg.agent.max_tokens)

    def test_make_provider_openai(self):
        from dataclasses import replace
        cfg = repo_config()
        p = make_provider(replace(cfg.agent, provider="openai_compat", model="glm-5", base_url="http://h/v1"), api_key="k")
        self.assertIsInstance(p, OpenAICompatProvider)
        self.assertEqual(p.url, "http://h/v1/chat/completions")


if __name__ == "__main__":
    unittest.main()
