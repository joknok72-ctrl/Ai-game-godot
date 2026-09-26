"""Provider request/response shapes — checked against the Claude Fable 5.1 docs (2026-09-26)."""
from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from _helpers import PRIVATE_ENV, anthropic_agent, repo_config

from godotai.providers import ProviderError, ToolCall, make_provider
from godotai.providers import cloudflare
from godotai.providers.anthropic import (API_VERSION, BETA_BINDING_CONTROLS, BETA_PER_MESSAGE_EFFORT,
                                         BETA_PROGRESS_UPDATES, BETA_TASK_BUDGETS, BETA_TURN_SCOPED_SYSTEM,
                                         AnthropicProvider)
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
        """Repo default: your own OpenAI-compatible server on 127.0.0.1:8000 — no vendor, no key needed."""
        cfg = repo_config()
        with mock.patch.dict(os.environ, PRIVATE_ENV):
            p = make_provider(cfg.agent)
        self.assertIsInstance(p, OpenAICompatProvider)
        self.assertEqual(p.model, "godotai")
        self.assertEqual(p.url, "http://127.0.0.1:8000/v1/chat/completions")
        self.assertEqual(p.effort, "max")
        self.assertEqual(p.max_tokens, cfg.agent.max_tokens)
        with mock.patch.dict(os.environ, {**PRIVATE_ENV, "OPENAI_BASE_URL": "http://gpu-box:8000/v1"}):
            self.assertEqual(make_provider(cfg.agent).url, "http://gpu-box:8000/v1/chat/completions")
        with mock.patch.dict(os.environ, {**PRIVATE_ENV, "OPENAI_API_KEY": "sk-test"}):
            self.assertEqual(make_provider(cfg.agent).url, "https://api.openai.com/v1/chat/completions",
                             "a vendor key alone is an explicit choice of the vendor endpoint")

    def test_make_provider_anthropic_opt_in(self):
        a = anthropic_agent()
        p = make_provider(a, api_key="k")
        self.assertIsInstance(p, AnthropicProvider)
        self.assertEqual(p.model, "claude-fable-5-1")
        self.assertEqual(p.effort, "max")
        self.assertEqual(p.max_tokens, a.max_tokens)

    def test_make_provider_openai(self):
        from dataclasses import replace
        cfg = repo_config()
        p = make_provider(replace(cfg.agent, provider="openai_compat", model="glm-5", base_url="http://h/v1"), api_key="k")
        self.assertIsInstance(p, OpenAICompatProvider)
        self.assertEqual(p.url, "http://h/v1/chat/completions")


class Fable51BetaShapeTests(unittest.TestCase):
    """Opt-in beta features — request shapes copied from the Fable 5.1 docs (read 2026-09-26)."""

    def test_defaults_send_no_beta_header_and_no_thinking_object(self):
        p = AnthropicProvider("claude-fable-5-1", api_key="k", effort="max")
        self.assertEqual(p.betas(), [])
        self.assertNotIn("anthropic-beta", p.headers())
        body = p.build_request(SYSTEM, [], TOOLS)
        self.assertNotIn("thinking", body)
        self.assertEqual(body["output_config"], {"effort": "max"})
        self.assertIsNone(p.effort_message("high"), "per-message effort is off unless enabled")

    def test_progress_updates(self):
        p = AnthropicProvider("claude-fable-5-1", api_key="k", progress_updates=True)
        self.assertEqual(p.headers()["anthropic-beta"], BETA_PROGRESS_UPDATES)
        body = p.build_request(SYSTEM, [], TOOLS)
        self.assertEqual(body["thinking"], {"type": "adaptive", "display": "updates"})
        self.assertNotIn("budget_tokens", body["thinking"])
        content = [{"type": "thinking", "thinking": "Reading the project files now.", "signature": "s"},
                   {"type": "thinking", "thinking": "", "signature": "s2"},
                   {"type": "tool_use", "id": "t1", "name": "list_files", "input": {}}]
        p.transport = FakeTransport({"type": "message", "content": content, "stop_reason": "tool_use", "usage": {},
                                     "input_transformations": [{"type": "thinking_blocks_dropped", "count": 1}]})
        turn = p.complete(SYSTEM, [], TOOLS)
        self.assertEqual(turn.progress, ["Reading the project files now."])
        self.assertEqual(turn.notices, [{"type": "thinking_blocks_dropped", "count": 1}])
        self.assertEqual(turn.raw_assistant_message["content"], content, "blocks are still replayed verbatim")
        # without the beta the same thinking text is NOT surfaced as progress
        q = AnthropicProvider("claude-fable-5-1", api_key="k")
        q.transport = p.transport
        self.assertEqual(q.complete(SYSTEM, [], TOOLS).progress, [])

    def test_task_budget(self):
        p = AnthropicProvider("claude-fable-5-1", api_key="k", effort="max", task_budget_tokens=200_000)
        self.assertEqual(p.headers()["anthropic-beta"], BETA_TASK_BUDGETS)
        body = p.build_request(SYSTEM, [], TOOLS)
        self.assertEqual(body["output_config"], {"effort": "max", "task_budget": {"type": "tokens", "total": 200000}})

    def test_turn_scoped_system_nudge_placement(self):
        call = ToolCall("t1", "list_files", {})
        plain = AnthropicProvider("claude-fable-5-1", api_key="k")
        msgs = plain.tool_results_message([(call, "a.gd", False)], nudge="NUDGE")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["content"][-1], {"type": "text", "text": "NUDGE"}, "documented non-beta placement")
        beta = AnthropicProvider("claude-fable-5-1", api_key="k", turn_scoped_system=True)
        self.assertEqual(beta.headers()["anthropic-beta"], BETA_TURN_SCOPED_SYSTEM)
        msgs = beta.tool_results_message([(call, "a.gd", False)], nudge="NUDGE")
        self.assertEqual(len(msgs), 2)
        self.assertEqual([b["type"] for b in msgs[0]["content"]], ["tool_result"])
        self.assertEqual(msgs[1], {"role": "system", "clear_at": "next_user_message", "content": "NUDGE"})
        self.assertEqual(len(beta.tool_results_message([(call, "x", False)], nudge=None)), 1)

    def test_per_message_effort_and_binding_controls(self):
        p = AnthropicProvider("claude-fable-5-1", api_key="k", effort="max", per_message_effort=True, prefix_binding_drop=True)
        self.assertEqual(p.headers()["anthropic-beta"], f"{BETA_PER_MESSAGE_EFFORT},{BETA_BINDING_CONTROLS}")
        self.assertEqual(p.effort_message("high"), {"role": "system", "content": [], "output_config": {"effort": "high"}})
        body = p.build_request(SYSTEM, [], TOOLS)
        self.assertEqual(body["thinking"], {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}})

    def test_all_betas_join_in_one_header(self):
        p = AnthropicProvider("claude-fable-5-1", api_key="k", progress_updates=True, task_budget_tokens=50_000,
                              turn_scoped_system=True, per_message_effort=True, prefix_binding_drop=True)
        header = p.headers()["anthropic-beta"]
        self.assertEqual(header.split(","), [BETA_PROGRESS_UPDATES, BETA_TASK_BUDGETS, BETA_TURN_SCOPED_SYSTEM,
                                             BETA_PER_MESSAGE_EFFORT, BETA_BINDING_CONTROLS])
        self.assertEqual(len(set(header.split(","))), 5)

    def test_extra_headers_are_sent(self):
        p = AnthropicProvider("claude-fable-5-1", api_key="k", extra_headers={"cf-aig-authorization": "Bearer <gw>"})
        self.assertEqual(p.headers()["cf-aig-authorization"], "Bearer <gw>")
        self.assertEqual(p.headers()["x-api-key"], "k")
        o = OpenAICompatProvider("m", api_key="k", base_url="http://x/v1", extra_headers={"X-Test": "1"})
        o.transport = FakeTransport({"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}]})
        o.complete(SYSTEM, [], TOOLS)
        self.assertEqual(o.transport.calls[0][1]["X-Test"], "1")
        self.assertEqual(o.transport.calls[0][1]["Authorization"], "Bearer k")

    def test_openai_compat_nudge_is_a_user_message(self):
        o = OpenAICompatProvider("m", api_key="k", base_url="http://x/v1")
        msgs = o.tool_results_message([(ToolCall("c1", "x", {}), "ok", False)], nudge="NUDGE")
        self.assertEqual(msgs[-1], {"role": "user", "content": "NUDGE"})
        self.assertIsNone(o.effort_message("high"))


class CloudflareRoutingTests(unittest.TestCase):
    """URL construction from env var *names* — no account id / token appears in the repo."""

    ENV = {"CF_ACCOUNT_ID": "acc123", "CF_AIG_GATEWAY": "godotai-gw", "CF_AIG_TOKEN": "", "CLOUDFLARE_API_TOKEN": ""}

    def test_gateway_urls_follow_the_documented_paths(self):
        with mock.patch.dict(os.environ, self.ENV, clear=False):
            self.assertEqual(cloudflare.gateway_base_url("anthropic"),
                             "https://gateway.ai.cloudflare.com/v1/acc123/godotai-gw/anthropic")
            self.assertEqual(cloudflare.gateway_base_url("compat"),
                             "https://gateway.ai.cloudflare.com/v1/acc123/godotai-gw/compat")
            self.assertEqual(cloudflare.workers_ai_base_url(), "https://api.cloudflare.com/client/v4/accounts/acc123/ai/v1")
            self.assertEqual(cloudflare.gateway_headers(), {}, "unauthenticated gateway → no extra header")
            with self.assertRaises(cloudflare.CloudflareRouteError):
                cloudflare.gateway_base_url("openai")
            with mock.patch.dict(os.environ, {"CF_WORKERS_AI_TOKEN": ""}):
                with self.assertRaises(cloudflare.CloudflareRouteError) as cm:
                    cloudflare.workers_ai_api_key()
                self.assertIn("CF_WORKERS_AI_TOKEN is not set", str(cm.exception))
        with mock.patch.dict(os.environ, {**self.ENV, "CF_AIG_TOKEN": "gw-secret"}):
            self.assertEqual(cloudflare.gateway_headers(), {"cf-aig-authorization": "Bearer gw-secret"})

    def test_workers_ai_token_prefers_the_dedicated_name(self):
        with mock.patch.dict(os.environ, {"CF_WORKERS_AI_TOKEN": " wai-only-token ", "CLOUDFLARE_API_TOKEN": "setup-token"}):
            self.assertEqual(cloudflare.workers_ai_api_key(), "wai-only-token", "the narrow token wins; whitespace is stripped")
        with mock.patch.dict(os.environ, {"CF_WORKERS_AI_TOKEN": "", "CLOUDFLARE_API_TOKEN": "legacy-token"}):
            self.assertEqual(cloudflare.workers_ai_api_key(), "legacy-token", "legacy name still works")

    def test_missing_env_gives_clear_error(self):
        with mock.patch.dict(os.environ, {"CF_ACCOUNT_ID": "", "CF_AIG_GATEWAY": ""}):
            with self.assertRaises(cloudflare.CloudflareRouteError) as cm:
                cloudflare.gateway_base_url("anthropic")
            self.assertIn("CF_ACCOUNT_ID is not set", str(cm.exception))

    def test_make_provider_routes(self):
        from dataclasses import replace
        cfg = repo_config()
        with mock.patch.dict(os.environ, {**self.ENV, "CF_AIG_TOKEN": "gw-secret", "CLOUDFLARE_API_TOKEN": "cf-secret"}):
            p = make_provider(anthropic_agent(route="cf_gateway"), api_key="anthropic-key")
            self.assertIsInstance(p, AnthropicProvider)
            self.assertEqual(p.url, "https://gateway.ai.cloudflare.com/v1/acc123/godotai-gw/anthropic/v1/messages")
            h = p.headers()
            self.assertEqual(h["x-api-key"], "anthropic-key", "the provider key still travels in x-api-key")
            self.assertEqual(h["cf-aig-authorization"], "Bearer gw-secret")

            o = make_provider(replace(cfg.agent, provider="openai_compat", model="gpt-x", route="cf_gateway"), api_key="k")
            self.assertEqual(o.url, "https://gateway.ai.cloudflare.com/v1/acc123/godotai-gw/compat/chat/completions")
            self.assertEqual(o.extra_headers, {"cf-aig-authorization": "Bearer gw-secret"})

            w = make_provider(replace(cfg.agent, provider="openai_compat", model="@cf/some/model", route="workers_ai"))
            self.assertEqual(w.url, "https://api.cloudflare.com/client/v4/accounts/acc123/ai/v1/chat/completions")
            self.assertEqual(w.api_key, "cf-secret")

            # an explicit base_url wins over the route-derived one
            e = make_provider(anthropic_agent(route="cf_gateway", base_url="https://proxy.example/x"), api_key="k")
            self.assertEqual(e.url, "https://proxy.example/x/v1/messages")

    def test_gateway_stored_key_needs_no_anthropic_key(self):
        """BYOK / unified billing: only cf-aig-authorization is sent (docs example, read 2026-09-26)."""
        with mock.patch.dict(os.environ, {**self.ENV, "CF_AIG_TOKEN": "gw-secret", "ANTHROPIC_API_KEY": ""}):
            p = make_provider(anthropic_agent(route="cf_gateway"))
            self.assertTrue(p.gateway_stored_key)
            self.assertNotIn("x-api-key", p.headers())
            self.assertEqual(p.headers()["cf-aig-authorization"], "Bearer gw-secret")
            p.transport = FakeTransport({"type": "message", "content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn", "usage": {}})
            self.assertEqual(p.complete(SYSTEM, [], TOOLS).text, "hi")
        with mock.patch.dict(os.environ, {**self.ENV, "ANTHROPIC_API_KEY": ""}):
            q = make_provider(anthropic_agent(route="cf_gateway"))   # unauthenticated gateway, no key anywhere
            self.assertFalse(q.gateway_stored_key)
            with self.assertRaises(ProviderError):
                q.complete(SYSTEM, [], TOOLS)

    def test_factory_passes_beta_flags(self):
        p = make_provider(anthropic_agent(progress_updates=True, task_budget_tokens=30_000, act_effort="high"), api_key="k")
        self.assertTrue(p.progress_updates)
        self.assertEqual(p.task_budget_tokens, 30_000)
        self.assertTrue(p.per_message_effort)
        self.assertIn(BETA_PER_MESSAGE_EFFORT, p.betas())
        q = make_provider(anthropic_agent(act_effort="max"), api_key="k")
        self.assertFalse(q.per_message_effort, "same effort → no beta header needed")


if __name__ == "__main__":
    unittest.main()
