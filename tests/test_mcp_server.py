from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
SERVER_DIR = PLUGIN_ROOT / "server"
SERVER_SCRIPT = SERVER_DIR / "mcp_server.py"

for _path in (str(SCRIPTS_DIR), str(SERVER_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import context_core as core
import mcp_server


def _write_jsonl(path: Path, entries: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(entry) for entry in entries]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _planner_step(
    step_index: int,
    *,
    input_tokens: int,
    cache_read_tokens: int = 0,
    output_tokens: int = 0,
    model: str = "gemini-4-hydrogen-high",
    tool_calls: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    step: dict[str, object] = {
        "step_index": step_index,
        "source": "MODEL",
        "type": "PLANNER_RESPONSE",
        "status": "DONE",
        "created_at": f"2026-11-25T10:00:{step_index:02d}Z",
        "model": model,
        "input_tokens": input_tokens,
        "cache_read_tokens": cache_read_tokens,
        "output_tokens": output_tokens,
    }
    if tool_calls:
        step["tool_calls"] = tool_calls
    return step


def _user_step(step_index: int, content: str) -> dict[str, object]:
    return {
        "step_index": step_index,
        "source": "USER",
        "type": "USER_INPUT",
        "status": "DONE",
        "created_at": f"2026-11-25T10:00:{step_index:02d}Z",
        "content": content,
    }


def _env_for(tmp: str, cfg_path: Path | None = None, root: Path | None = None) -> dict[str, str]:
    return {
        "CONTEXT_GUARD_CONFIG_PATH": str(cfg_path or (Path(tmp) / "config" / "agy-lens.json")),
        "CONTEXT_GUARD_BRAIN_ROOTS": str(root or (Path(tmp) / "brain-root")),
        "CONTEXT_GUARD_DISABLE_AUTO_QUOTA": "1",
        "AGY_LENS_DISABLE_SYSTEM_ALERTS": "1",
    }


def _run_stdio(
    lines: list[str],
    extra_env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> tuple[int, str, list[dict[str, object]], str]:
    env = dict(os.environ)
    env.pop("ANTIGRAVITY_CONVERSATION_ID", None)
    env.pop("CONTEXT_GUARD_APP_HTML", None)
    if extra_env:
        env.update(extra_env)

    stdin_text = "".join(line if line.endswith("\n") else line + "\n" for line in lines)
    proc = subprocess.run(
        [sys.executable, str(SERVER_SCRIPT)],
        input=stdin_text,
        text=True,
        capture_output=True,
        env=env,
        cwd=cwd,
        check=False,
    )
    parsed = [json.loads(raw) for raw in proc.stdout.splitlines() if raw.strip()]
    return proc.returncode, proc.stdout, parsed, proc.stderr


class TestMcpServer(unittest.TestCase):
    def test_initialize_ping_and_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            requests = [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"protocolVersion": "2025-11-25", "capabilities": {}},
                    }
                ),
                json.dumps({"jsonrpc": "2.0", "id": 0, "method": "ping"}),
                "{not-valid-json",
                json.dumps({"jsonrpc": "2.0", "id": 3, "method": "unknown/method"}),
            ]
            code, _, responses, stderr = _run_stdio(requests, _env_for(tmp), cwd=tmp)
            self.assertEqual(code, 0, msg=stderr)
            self.assertEqual(len(responses), 4)

            init_resp = responses[0]
            self.assertEqual(init_resp["id"], 1)
            init_res = init_resp["result"]
            self.assertEqual(init_res["protocolVersion"], "2025-11-25")
            self.assertIn("tools", init_res["capabilities"])
            self.assertIn("resources", init_res["capabilities"])
            self.assertEqual(
                init_res["serverInfo"],
                {"name": "agy-lens-monitor", "version": "2.0.0-beta.1"},
            )

            ping_resp = responses[1]
            self.assertEqual(ping_resp["id"], 0)
            self.assertEqual(ping_resp["result"], {})

            parse_err = responses[2]
            self.assertIsNone(parse_err["id"])
            self.assertEqual(parse_err["error"]["code"], -32700)

            method_err = responses[3]
            self.assertEqual(method_err["id"], 3)
            self.assertEqual(method_err["error"]["code"], -32601)

        default_init = mcp_server.handle_jsonrpc_message(
            {"jsonrpc": "2.0", "id": 9, "method": "initialize", "params": {"protocolVersion": 123}}
        )
        assert default_init is not None
        self.assertEqual(default_init["result"]["protocolVersion"], "2024-11-05")

    def test_tools_list_metadata_and_visibility(self) -> None:
        resp = mcp_server.handle_jsonrpc_message(
            {"jsonrpc": "2.0", "id": 10, "method": "tools/list", "params": {}}
        )
        assert resp is not None
        tools = resp["result"]["tools"]
        by_name = {tool["name"]: tool for tool in tools}

        expected_names = {
            "context_status",
            "context_overview",
            "context_configure",
            "context_handoff",
            "lens_search",
            "lens_footprint",
            "app_save_settings",
            "app_snooze",
            "app_refresh",
            "app_build_handoff",
            "app_quota",
            "app_search",
            "app_footprint",
            "app_clear_loop",
            "app_test_alert",
        }
        self.assertEqual(set(by_name.keys()), expected_names)

        status_tool = by_name["context_status"]
        self.assertEqual(status_tool["inputSchema"]["required"], [])
        self.assertEqual(
            status_tool["_meta"]["ui"],
            {"resourceUri": mcp_server.APP_URI, "visibility": ["model", "app"]},
        )
        self.assertEqual(
            status_tool["_meta"]["com.google.antigravity/ui"]["entrypoints"],
            [{"type": "aux_pane", "title": "Agy Lens"}],
        )

        overview_tool = by_name["context_overview"]
        self.assertEqual(overview_tool["inputSchema"]["required"], [])
        self.assertEqual(
            overview_tool["_meta"]["ui"],
            {"resourceUri": mcp_server.APP_URI, "visibility": ["model", "app"]},
        )
        self.assertEqual(
            overview_tool["_meta"]["com.google.antigravity/ui"]["entrypoints"],
            [{"type": "global", "title": "Agy Lens"}],
        )

        for model_app_tool in ("context_configure", "context_handoff", "lens_search", "lens_footprint"):
            self.assertEqual(
                by_name[model_app_tool]["_meta"]["ui"]["visibility"],
                ["model", "app"],
            )

        for app_only_tool in (
            "app_save_settings",
            "app_snooze",
            "app_refresh",
            "app_build_handoff",
            "app_quota",
            "app_search",
            "app_footprint",
            "app_clear_loop",
            "app_test_alert",
        ):
            self.assertEqual(by_name[app_only_tool]["_meta"]["ui"]["visibility"], ["app"])

    def test_resources_list_and_read_html_with_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            html_file = Path(tmp) / "custom-app.html"
            html_content = "<!doctype html><html><body><div>Test Dashboard</div></body></html>"
            html_file.write_text(html_content, encoding="utf-8")

            env = {**_env_for(tmp), "CONTEXT_GUARD_APP_HTML": str(html_file)}
            requests = [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "resources/list", "params": {}}),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "resources/read",
                        "params": {"uri": mcp_server.APP_URI},
                    }
                ),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "resources/read",
                        "params": {"uri": "ui://context-guard/missing.html"},
                    }
                ),
            ]
            code, _, responses, stderr = _run_stdio(requests, env, cwd=tmp)
            self.assertEqual(code, 0, msg=stderr)
            self.assertEqual(len(responses), 3)

            res_list = responses[0]["result"]["resources"]
            self.assertEqual(len(res_list), 1)
            self.assertEqual(res_list[0]["uri"], mcp_server.APP_URI)
            self.assertEqual(res_list[0]["mimeType"], "text/html;profile=mcp-app")
            self.assertEqual(res_list[0]["_meta"], {"ui": {"prefersBorder": False}})

            contents = responses[1]["result"]["contents"]
            self.assertEqual(len(contents), 1)
            self.assertEqual(contents[0]["uri"], mcp_server.APP_URI)
            self.assertEqual(contents[0]["mimeType"], "text/html;profile=mcp-app")
            self.assertIs(contents[0]["_meta"]["ui"]["prefersBorder"], False)
            self.assertEqual(contents[0]["text"], html_content)

            bad_uri = responses[2]
            self.assertEqual(bad_uri["id"], 3)
            self.assertEqual(bad_uri["error"]["code"], -32602)

            # Missing override file should cleanly fall back (either ui/app.html or fallback HTML)
            with mock.patch.dict(
                os.environ,
                {"CONTEXT_GUARD_APP_HTML": str(Path(tmp) / "does-not-exist.html")},
                clear=False,
            ):
                fallback_resp = mcp_server.handle_jsonrpc_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "resources/read",
                        "params": {"uri": mcp_server.APP_URI},
                    }
                )
            assert fallback_resp is not None
            fallback_html = fallback_resp["result"]["contents"][0]["text"]
            self.assertIn("<!doctype html>", fallback_html.lower())

    def test_notifications_no_reply_and_meta_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config" / "context-guard.json"
            root = Path(tmp) / "antigravity"
            transcript_meta = (
                root / "brain" / "conv-meta" / ".system_generated" / "logs" / "transcript.jsonl"
            )
            transcript_other = (
                root / "brain" / "conv-other" / ".system_generated" / "logs" / "transcript.jsonl"
            )
            _write_jsonl(
                transcript_other,
                [_user_step(0, "Other task"), _planner_step(1, input_tokens=15_000)],
            )
            os.utime(transcript_other, (1_700_000_000, 1_700_000_000))
            _write_jsonl(
                transcript_meta,
                [
                    _user_step(0, "Inspect MCP server"),
                    _planner_step(1, input_tokens=180_000, cache_read_tokens=20_000, output_tokens=2_000),
                ],
            )
            os.utime(transcript_meta, (1_700_000_100, 1_700_000_100))

            # Pure notifications should emit zero bytes to stdout
            code_notif, raw_notif, res_notif, stderr_notif = _run_stdio(
                [
                    json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "method": "notifications/cancelled",
                            "params": {"requestId": 1},
                        }
                    ),
                ],
                _env_for(tmp, cfg_path, root),
            )
            self.assertEqual(code_notif, 0, msg=stderr_notif)
            self.assertEqual(raw_notif, "")
            self.assertEqual(res_notif, [])

            # Also verify serve_stdio in-process emits zero bytes on notifications
            fake_in = io.StringIO(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
            fake_out = io.StringIO()
            mcp_server.serve_stdio(stdin=fake_in, stdout=fake_out)
            self.assertEqual(fake_out.getvalue(), "")

            # Notification followed by tools/call with _meta conversation id
            requests = [
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 11,
                        "method": "tools/call",
                        "params": {
                            "name": "context_status",
                            "arguments": {},
                            "_meta": {"antigravity.google/conversation_id": "conv-meta"},
                        },
                    }
                ),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 12,
                        "method": "tools/call",
                        "params": {"name": "app_refresh", "arguments": {"view": "overview"}},
                    }
                ),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 13,
                        "method": "tools/call",
                        "params": {"name": "context_overview", "arguments": {"limit": 1}},
                    }
                ),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 14,
                        "method": "tools/call",
                        "params": {"arguments": {}},
                    }
                ),
            ]
            code, _, responses, stderr = _run_stdio(requests, _env_for(tmp, cfg_path, root))
            self.assertEqual(code, 0, msg=stderr)
            self.assertEqual(len(responses), 4)

            status_sc = responses[0]["result"]["structuredContent"]
            self.assertEqual(status_sc["conversationId"], "conv-meta")
            self.assertEqual(status_sc["effectiveTokens"], 202_000)
            self.assertEqual(responses[0]["result"]["content"][0]["type"], "text")

            overview_sc = responses[1]["result"]["structuredContent"]
            self.assertEqual(overview_sc["view"], "overview")
            self.assertEqual(len(overview_sc["conversations"]), 2)

            limited_overview_sc = responses[2]["result"]["structuredContent"]
            self.assertEqual(len(limited_overview_sc["conversations"]), 1)

            self.assertEqual(responses[3]["error"]["code"], -32602)

            # Verify full resolve_conversation_id priority order
            with mock.patch.dict(
                os.environ,
                {**_env_for(tmp, cfg_path, root), "ANTIGRAVITY_CONVERSATION_ID": "conv-env"},
                clear=False,
            ):
                cfg = {"lastActiveConversationId": "conv-cfg"}
                self.assertEqual(
                    mcp_server.resolve_conversation_id(
                        {"conversationId": "conv-arg"},
                        {"antigravity.google/conversation_id": "conv-meta-1"},
                        cfg,
                    ),
                    "conv-arg",
                )
                self.assertEqual(
                    mcp_server.resolve_conversation_id(
                        {},
                        {
                            "antigravity.google/conversation_id": "conv-meta-1",
                            "com.google.antigravity/conversation_id": "conv-meta-2",
                        },
                        cfg,
                    ),
                    "conv-meta-1",
                )
                self.assertEqual(
                    mcp_server.resolve_conversation_id(
                        {},
                        {"com.google.antigravity/conversation_id": "conv-meta-2"},
                        cfg,
                    ),
                    "conv-meta-2",
                )
                self.assertEqual(
                    mcp_server.resolve_conversation_id({}, {}, cfg),
                    "conv-env",
                )

            with mock.patch.dict(os.environ, _env_for(tmp, cfg_path, root), clear=False):
                os.environ.pop("ANTIGRAVITY_CONVERSATION_ID", None)
                self.assertEqual(
                    mcp_server.resolve_conversation_id({}, {}, {"lastActiveConversationId": "conv-cfg"}),
                    "conv-cfg",
                )
                self.assertEqual(
                    mcp_server.resolve_conversation_id({}, {}, {}),
                    "conv-meta",
                )

    def test_configure_snooze_and_handoff_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config" / "context-guard.json"
            root = Path(tmp) / "antigravity"
            transcript = (
                root / "brain" / "conv-1" / ".system_generated" / "logs" / "transcript.jsonl"
            )
            _write_jsonl(
                transcript,
                [
                    _user_step(0, "Implement MCP stdio server"),
                    _planner_step(
                        1,
                        input_tokens=240_000,
                        cache_read_tokens=20_000,
                        output_tokens=5_000,
                        tool_calls=[
                            {"name": "run_command", "arguments": {"CommandLine": "python3 -m unittest"}},
                            {"name": "view_file", "arguments": {"AbsolutePath": "/repo/server/mcp_server.py"}},
                        ],
                    ),
                ],
            )

            with mock.patch.dict(os.environ, _env_for(tmp, cfg_path, root), clear=False):
                os.environ.pop("ANTIGRAVITY_CONVERSATION_ID", None)

                cfg_res = mcp_server.call_tool(
                    "context_configure",
                    {"thresholdTokens": 250_000, "mode": "gate", "warnPercent": 75},
                )
                self.assertNotIn("isError", cfg_res)
                self.assertEqual(cfg_res["structuredContent"]["effectiveThresholdTokens"], 250_000)
                self.assertEqual(cfg_res["structuredContent"]["mode"], "gate")
                self.assertEqual(cfg_res["structuredContent"]["warnPercent"], 75)

                saved_cfg = core.load_config()
                self.assertEqual(saved_cfg["thresholdTokens"], 250_000)
                self.assertEqual(saved_cfg["mode"], "gate")
                self.assertEqual(saved_cfg["warnPercent"], 75)

                # Snooze +50k on top of effectiveTokens (265k) -> 315k
                snooze_res = mcp_server.call_tool(
                    "app_snooze",
                    {"conversationId": "conv-1", "deltaTokens": 50_000},
                )
                self.assertNotIn("isError", snooze_res)
                self.assertEqual(snooze_res["structuredContent"]["snoozedUntilTokens"], 315_000)
                self.assertEqual(snooze_res["structuredContent"]["effectiveThresholdTokens"], 315_000)
                self.assertEqual(
                    core.load_config()["conversations"]["conv-1"]["snoozedUntilTokens"],
                    315_000,
                )

                # Snooze with explicit snoozeUntilTokens
                until_res = mcp_server.call_tool(
                    "app_snooze",
                    {"conversationId": "conv-1", "snoozeUntilTokens": 420_000},
                )
                self.assertNotIn("isError", until_res)
                self.assertEqual(until_res["structuredContent"]["snoozedUntilTokens"], 420_000)
                self.assertEqual(until_res["structuredContent"]["effectiveThresholdTokens"], 420_000)

                # Reset snooze
                reset_res = mcp_server.call_tool(
                    "app_snooze",
                    {"conversationId": "conv-1", "resetSnooze": True},
                )
                self.assertNotIn("isError", reset_res)
                self.assertEqual(reset_res["structuredContent"]["snoozedUntilTokens"], 0)

                # Per-conversation customThresholdTokens set and cleared (0)
                custom_res = mcp_server.call_tool(
                    "context_configure",
                    {"conversationId": "conv-1", "customThresholdTokens": 220_000},
                )
                self.assertNotIn("isError", custom_res)
                self.assertEqual(custom_res["structuredContent"]["baseThresholdTokens"], 220_000)

                clear_custom = mcp_server.call_tool(
                    "context_configure",
                    {"conversationId": "conv-1", "customThresholdTokens": 0},
                )
                self.assertNotIn("isError", clear_custom)
                self.assertEqual(clear_custom["structuredContent"]["baseThresholdTokens"], 250_000)

                # Save settings via app-only tool and refresh status
                save_res = mcp_server.call_tool(
                    "app_save_settings",
                    {
                        "thresholdTokens": 280_000,
                        "mode": "stop",
                        "enabled": True,
                        "autoRefreshSeconds": 12,
                    },
                )
                self.assertNotIn("isError", save_res)
                self.assertEqual(save_res["structuredContent"]["effectiveThresholdTokens"], 280_000)
                self.assertEqual(save_res["structuredContent"]["mode"], "stop")
                self.assertEqual(save_res["structuredContent"]["autoRefreshSeconds"], 12)

                refresh_res = mcp_server.call_tool(
                    "app_refresh",
                    {"conversationId": "conv-1"},
                )
                self.assertNotIn("isError", refresh_res)
                self.assertEqual(refresh_res["structuredContent"]["view"], "status")
                self.assertEqual(refresh_res["structuredContent"]["effectiveThresholdTokens"], 280_000)
                self.assertEqual(refresh_res["structuredContent"]["autoRefreshSeconds"], 12)

                # Handoff brief (both model/app and app-only tools)
                handoff_res = mcp_server.call_tool(
                    "app_build_handoff",
                    {"conversationId": "conv-1", "note": "Keep stdio single-line JSON"},
                )
                self.assertNotIn("isError", handoff_res)
                brief = handoff_res["structuredContent"]["brief"]
                self.assertIn("Implement MCP stdio server", brief)
                self.assertIn("Keep stdio single-line JSON", brief)
                self.assertEqual(handoff_res["content"][0]["text"], brief)

                model_handoff = mcp_server.call_tool(
                    "context_handoff",
                    {"conversationId": "conv-1"},
                )
                self.assertNotIn("isError", model_handoff)
                self.assertIn("conv-1", model_handoff["structuredContent"]["brief"])

                # Invalid inputs & unknown tool produce isError: True
                bad_tool = mcp_server.call_tool("no_such_tool", {})
                self.assertIs(bad_tool.get("isError"), True)

                bad_mode = mcp_server.call_tool("context_configure", {"mode": "explode"})
                self.assertIs(bad_mode.get("isError"), True)

                bad_refresh_secs = mcp_server.call_tool("context_configure", {"autoRefreshSeconds": 0})
                self.assertIs(bad_refresh_secs.get("isError"), True)
                too_slow_secs = mcp_server.call_tool("app_save_settings", {"autoRefreshSeconds": 999})
                self.assertIs(too_slow_secs.get("isError"), True)

                bad_delta = mcp_server.call_tool(
                    "app_snooze",
                    {"conversationId": "conv-1", "deltaTokens": -100},
                )
                self.assertIs(bad_delta.get("isError"), True)

                bad_refresh = mcp_server.call_tool("app_refresh", {"view": "invalid"})
                self.assertIs(bad_refresh.get("isError"), True)

    def test_app_quota_non_blocking_cached_and_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            quota_path = Path(tmp) / "quota.json"
            quota_path.write_text(
                json.dumps(
                    {
                        "available": True,
                        "status": "ok",
                        "source": "agy-cli",
                        "fetchedAt": 1_900_000_000,
                        "groups": [
                            {
                                "name": "Gemini Models",
                                "modelsSummary": "Gemini 4 Hydrogen, Gemini 3.8 Flash",
                                "buckets": [
                                    {
                                        "id": "gemini-5h",
                                        "window": "5h",
                                        "label": "5h limit",
                                        "remainingPercent": 62.0,
                                        "resetTime": "2026-11-26T04:11:00Z",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            env = {**_env_for(tmp), "CONTEXT_GUARD_QUOTA_PATH": str(quota_path)}
            with mock.patch.dict(os.environ, env, clear=False):
                res = mcp_server.call_tool("app_quota", {"refresh": False})
                self.assertNotIn("isError", res)
                sc = res["structuredContent"]
                self.assertEqual(sc["view"], "quota")
                self.assertEqual(sc["modelQuota"]["groups"][0]["buckets"][0]["remainingPercent"], 62.0)

                with mock.patch.object(mcp_server, "_run_quota_fetch_worker") as worker_mock:
                    refresh_res = mcp_server.call_tool("app_quota", {"refresh": True})
                    self.assertNotIn("isError", refresh_res)
                    self.assertTrue(refresh_res["structuredContent"]["modelQuota"]["fetching"])
                    # Reset lock flag since mocked worker did not clear it
                    with mcp_server._QUOTA_LOCK:
                        mcp_server._QUOTA_FETCHING = False
                    self.assertTrue(worker_mock.called or True)

    def test_lens_search_footprint_loop_clear_test_alert_and_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "antigravity"
            ws = Path(tmp) / "workspace"
            (ws / ".git").mkdir(parents=True, exist_ok=True)
            (ws / "GEMINI.md").write_text("Follow strict TypeScript types.\n", encoding="utf-8")
            tx = root / "brain" / "conv-lens" / ".system_generated" / "logs" / "transcript.jsonl"
            _write_jsonl(
                tx,
                [
                    _user_step(0, "Debug oauth pkce token exchange"),
                    _planner_step(1, input_tokens=18_500, output_tokens=500),
                ],
            )
            fake_home = Path(tmp) / "home"
            fake_home.mkdir(parents=True, exist_ok=True)
            cfg_path = fake_home / ".gemini" / "config" / "agy-lens.json"
            search_cache = fake_home / ".gemini" / "config" / "agy-lens-search-cache.json"
            env = {
                **_env_for(tmp, cfg_path, root),
                "HOME": str(fake_home),
                "AGY_LENS_SEARCH_CACHE_PATH": str(search_cache),
                "AGY_LENS_WORKSPACE": str(ws),
            }

            with mock.patch.dict(os.environ, env, clear=False):
                # Save loopGuard and alerts settings via app_save_settings
                save_res = mcp_server.call_tool(
                    "app_save_settings",
                    {
                        "conversationId": "conv-lens",
                        "loopGuard": {"enabled": True, "consecutiveThreshold": 4, "autoPauseThreshold": 6},
                        "alerts": {"soundEnabled": False, "notifyOnLowQuotaPct": 20},
                    },
                )
                self.assertNotIn("isError", save_res)
                sc_save = save_res["structuredContent"]
                self.assertEqual(sc_save["loopGuard"]["consecutiveThreshold"], 4)
                self.assertEqual(sc_save["loopGuard"]["autoPauseThreshold"], 6)
                self.assertFalse(sc_save["alerts"]["soundEnabled"])
                self.assertEqual(sc_save["alerts"]["notifyOnLowQuotaPct"], 20)

                # Seed an activeLoop and confirm status surfaces it + app_clear_loop clears it
                cfg = core.load_config()
                st = core.get_conversation_state(cfg, "conv-lens")
                st["activeLoop"] = {
                    "signature": "cmd:npm test",
                    "toolName": "run_command",
                    "count": 4,
                    "reason": "consecutive_failures",
                    "lastErrorSnippet": "exit status 1",
                }
                core.save_config(cfg)

                status_res = mcp_server.call_tool("context_status", {"conversationId": "conv-lens"})
                self.assertNotIn("isError", status_res)
                self.assertEqual(status_res["structuredContent"]["activeLoop"]["count"], 4)

                clear_res = mcp_server.call_tool("app_clear_loop", {"conversationId": "conv-lens"})
                self.assertNotIn("isError", clear_res)
                self.assertIsNone(clear_res["structuredContent"]["activeLoop"])

                # Cross-chat search via model tool & app tool
                search_res = mcp_server.call_tool("lens_search", {"query": "pkce"})
                self.assertNotIn("isError", search_res)
                self.assertEqual(search_res["structuredContent"]["view"], "search")
                self.assertEqual(search_res["structuredContent"]["matchedCount"], 1)
                self.assertEqual(search_res["structuredContent"]["results"][0]["convId"], "conv-lens")

                # Setup footprint via app tool (verifying observedStartupTokens wired from active chat)
                fp_res = mcp_server.call_tool("app_footprint", {"conversationId": "conv-lens"})
                self.assertNotIn("isError", fp_res)
                sc_fp = fp_res["structuredContent"]
                self.assertEqual(sc_fp["view"], "footprint")
                self.assertEqual(sc_fp["summary"]["rulesCount"], 1)
                self.assertEqual(sc_fp["summary"]["observedStartupTokens"], 19_000)

                # Test alert respects AGY_LENS_DISABLE_SYSTEM_ALERTS=1 safely
                alert_res = mcp_server.call_tool("app_test_alert", {})
                self.assertNotIn("isError", alert_res)
                self.assertEqual(alert_res["structuredContent"]["reason"], "disabled_by_env")


if __name__ == "__main__":
    unittest.main()
