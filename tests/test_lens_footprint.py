from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def _write_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


class TestLensFootprint(unittest.TestCase):
    def _isolated_env(self, home: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = {
            "HOME": str(home),
            "AGY_LENS_CONFIG_PATH": str(home / ".gemini" / "config" / "agy-lens.json"),
            "AGY_LENS_BRAIN_ROOTS": str(home / ".gemini" / "antigravity"),
        }
        if extra:
            env.update(extra)
        return env

    def test_scans_global_and_workspace_hierarchy_separating_frontmatter_and_body_tokens(self) -> None:
        import lens_footprint as lf

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            outside_parent = root / "outside"
            repo = outside_parent / "repo"
            mid = repo / "packages"
            leaf = mid / "app"
            (repo / ".git").mkdir(parents=True)
            leaf.mkdir(parents=True)

            # Should NOT be scanned because ancestor walk stops at repo/.git.
            _write_text(outside_parent / "GEMINI.md", "# Outside Repo\nDo not scan me.\n")

            # Global rules
            _write_text(home / ".gemini" / "GEMINI.md", "Global Gemini instructions.")
            _write_text(home / ".gemini" / "AGENTS.md", "Global Agents instructions.")
            _write_text(home / ".gemini" / "config" / "rules" / "01-style.md", "Use clean Python.")

            # Enabled plugin with rule, skill, mcp, hooks
            plugin_dir = home / ".gemini" / "config" / "plugins" / "sample-plugin"
            _write_json(plugin_dir / "plugin.json", {"name": "sample-plugin", "version": "1.2.3"})
            _write_text(plugin_dir / "rules" / "plugin-rule.md", "Plugin rule text.")
            _write_text(
                plugin_dir / "skills" / "plug-skill" / "SKILL.md",
                "---\nname: plug-skill\ndescription: Short plugin skill.\n---\n\n" + ("Plugin body detail. " * 20),
            )
            _write_json(
                plugin_dir / "mcp_config.json",
                {"mcpServers": {"plugin-mcp": {"command": "python3", "args": ["srv.py"]}}},
            )
            _write_json(
                plugin_dir / "hooks.json",
                {
                    "sample-plugin": {
                        "enabled": True,
                        "SessionStart": [{"type": "command", "command": "echo hi", "timeout": 5}],
                    }
                },
            )

            # Disabled plugin via plugins.json (must not contribute active rules/skills/mcp/hooks)
            disabled_dir = home / ".gemini" / "config" / "plugins" / "muted-plugin"
            _write_json(disabled_dir / "plugin.json", {"name": "muted-plugin", "version": "0.1.0"})
            _write_text(disabled_dir / "rules" / "ignored.md", "Ignored rule " * 50)
            _write_text(
                disabled_dir / "skills" / "muted-skill" / "SKILL.md",
                "---\nname: muted-skill\ndescription: Ignored\n---\nIgnored body",
            )
            _write_json(
                disabled_dir / "mcp_config.json",
                {"mcpServers": {"muted-mcp": {"command": "node", "args": ["muted.js"]}}},
            )
            _write_json(home / ".gemini" / "config" / "plugins.json", {"disabled": ["muted-plugin"]})

            # Global skills (~/.gemini/config/skills & ~/.agents/skills)
            _write_text(
                home / ".gemini" / "config" / "skills" / "user-skill" / "SKILL.md",
                (
                    "---\n"
                    "name: user-skill\n"
                    "description: >-\n"
                    "  Multi-line yaml description for\n"
                    "  the user skill.\n"
                    "---\n\n"
                    + ("Detailed instructions for user skill. " * 25)
                ),
            )
            _write_text(
                home / ".agents" / "skills" / "agent-skill" / "SKILL.md",
                "---\nname: agent-skill\ndescription: Shared agent skill.\n---\nBody content here.",
            )

            # Built-in skills (aggregated into builtinSkillsSummary only)
            _write_text(
                home / ".gemini" / "antigravity" / "builtin" / "skills" / "builtin-a" / "SKILL.md",
                "---\nname: builtin-a\ndescription: Builtin helper A.\n---\n" + ("Builtin A body " * 20),
            )
            _write_text(
                home / ".gemini" / "antigravity" / "builtin" / "skills" / "builtin-b" / "SKILL.md",
                "---\nname: builtin-b\ndescription: Builtin helper B.\n---\n" + ("Builtin B body " * 20),
            )

            # Workspace hierarchy rules, skills, hooks
            _write_text(repo / "GEMINI.md", "Repo root Gemini guide.")
            _write_text(mid / "AGENTS.md", "Packages agents guide.")
            _write_text(leaf / ".agents" / "rules" / "app-rule.md", "App rule body.")
            _write_json(leaf / ".agents" / "rules.json", {"rules": ["keep-it-simple"]})
            _write_text(
                leaf / ".agents" / "skills" / "ws-skill" / "SKILL.md",
                "---\nname: ws-skill\ndescription: Workspace skill.\n---\n" + ("Workspace instructions. " * 15),
            )
            _write_json(
                leaf / ".agents" / "hooks.json",
                {
                    "ws-guard": {
                        "enabled": True,
                        "PostToolUse": [
                            {
                                "matcher": "*",
                                "hooks": [{"type": "command", "command": "python3 check.py", "timeout": 7}],
                            }
                        ],
                    }
                },
            )

            with mock.patch.dict(os.environ, self._isolated_env(home), clear=True):
                result = lf.inspect_footprint(
                    workspace_path=str(leaf),
                    observed_startup_tokens=4321,
                )

            self.assertEqual(result["workspacePath"], str(leaf.resolve()))

            rule_paths = [r["path"] for r in result["rules"]]
            self.assertFalse(any("outside" in p and "GEMINI.md" in p and "repo" not in p for p in rule_paths))
            self.assertFalse(any("muted-plugin" in p for p in rule_paths))
            # Workspace rules ordered root-to-leaf
            ws_rules = [r for r in result["rules"] if r["scope"] == "workspace"]
            self.assertEqual(
                [Path(r["path"]).name for r in ws_rules],
                ["GEMINI.md", "AGENTS.md", "app-rule.md", "rules.json"],
            )
            self.assertTrue(all(r["alwaysLoaded"] is True for r in result["rules"]))

            # Plugins
            plugins_by_id = {p["id"]: p for p in result["plugins"]}
            self.assertIn("sample-plugin", plugins_by_id)
            self.assertIn("muted-plugin", plugins_by_id)
            self.assertTrue(plugins_by_id["sample-plugin"]["enabled"])
            self.assertFalse(plugins_by_id["muted-plugin"]["enabled"])
            self.assertTrue(plugins_by_id["sample-plugin"]["hasMcp"])
            self.assertTrue(plugins_by_id["sample-plugin"]["hasHooks"])
            self.assertEqual(plugins_by_id["sample-plugin"]["skillsCount"], 1)
            self.assertEqual(plugins_by_id["sample-plugin"]["rulesCount"], 1)

            # Skills: non-builtin only in skills[], builtins aggregated
            skill_names = {s["name"] for s in result["skills"]}
            self.assertEqual(skill_names, {"plug-skill", "user-skill", "agent-skill", "ws-skill"})
            user_skill = next(s for s in result["skills"] if s["name"] == "user-skill")
            self.assertIn("Multi-line yaml description", user_skill["description"])
            self.assertGreater(user_skill["frontmatterTokens"], 0)
            self.assertGreater(user_skill["bodyTokens"], user_skill["frontmatterTokens"])

            builtin_summary = result["builtinSkillsSummary"]
            self.assertEqual(builtin_summary["count"], 2)
            self.assertGreater(builtin_summary["frontmatterTokens"], 0)
            self.assertGreater(builtin_summary["onDemandTokens"], builtin_summary["frontmatterTokens"])

            # Hooks
            hooks_by_name = {h["name"]: h for h in result["hooks"]}
            self.assertIn("sample-plugin", hooks_by_name)
            self.assertIn("ws-guard", hooks_by_name)
            self.assertEqual(hooks_by_name["ws-guard"]["events"], ["PostToolUse"])
            self.assertEqual(hooks_by_name["ws-guard"]["timeoutSeconds"], 7)

            # Summary math
            summary = result["summary"]
            expected_startup = (
                sum(r["estTokens"] for r in result["rules"])
                + sum(s["frontmatterTokens"] for s in result["skills"])
                + builtin_summary["frontmatterTokens"]
                + sum(m["estTokens"] for m in result["mcpServers"] if m["enabled"])
            )
            expected_on_demand = (
                sum(s["bodyTokens"] for s in result["skills"]) + builtin_summary["onDemandTokens"]
            )
            self.assertEqual(summary["estimatedStartupTokens"], expected_startup)
            self.assertEqual(summary["onDemandSkillsTokens"], expected_on_demand)
            self.assertEqual(summary["observedStartupTokens"], 4321)
            self.assertEqual(summary["rulesCount"], len(result["rules"]))
            self.assertEqual(summary["pluginsCount"], 2)
            self.assertEqual(summary["skillsCount"], 4)
            self.assertEqual(summary["builtinSkillsCount"], 2)
            self.assertEqual(summary["mcpServersCount"], 1)
            self.assertEqual(summary["hooksCount"], 2)

    def test_masks_mcp_env_secrets_completely(self) -> None:
        import lens_footprint as lf

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            ws = root / "ws"
            ws.mkdir(parents=True)

            _write_json(
                home / ".gemini" / "config" / "mcp_config.json",
                {
                    "mcpServers": {
                        "secret-srv": {
                            "command": "npx",
                            "args": [
                                "-y",
                                "@example/mcp",
                                "-H",
                                "Authorization: Bearer bearer-secret-token-777",
                                "--token=inline-token-888",
                                "--api-key",
                                "flag-secret-999",
                                "https://user:urlpass-444@mcp.example.com/sse?api_key=query-secret-333",
                            ],
                            "env": {
                                "OPENAI_API_KEY": "sk-secret-value-999",
                                "DB_PASS": "db-secret-pass-111",
                            },
                            "headers": {
                                "X-Api-Key": "header-secret-555",
                            },
                        },
                        "remote-srv": {
                            "serverUrl": "https://user:remote-pass-222@api.example.com/mcp?token=remote-q-111",
                            "headers": {"Authorization": "Bearer remote-hdr-666"},
                        },
                        "disabled-srv": {
                            "disabled": True,
                            "command": "python3",
                            "args": ["off.py"],
                            "env": {"SECRET_TOKEN": "disabled-secret-000"},
                        },
                    }
                },
            )

            with mock.patch.dict(os.environ, self._isolated_env(home), clear=True):
                result = lf.inspect_footprint(workspace_path=str(ws))

            serialized = json.dumps(result)
            for secret in (
                "sk-secret-value-999",
                "db-secret-pass-111",
                "bearer-secret-token-777",
                "inline-token-888",
                "flag-secret-999",
                "header-secret-555",
                "disabled-secret-000",
                "urlpass-444",
                "query-secret-333",
                "remote-pass-222",
                "remote-q-111",
                "remote-hdr-666",
            ):
                self.assertNotIn(secret, serialized)

            by_name = {m["name"]: m for m in result["mcpServers"]}
            self.assertEqual(by_name["secret-srv"]["envKeys"], ["DB_PASS", "OPENAI_API_KEY"])
            self.assertEqual(by_name["secret-srv"]["envKeyCount"], 2)
            self.assertTrue(by_name["secret-srv"]["enabled"])
            self.assertEqual(by_name["secret-srv"]["estTokens"], 350)
            self.assertIn("npx", by_name["secret-srv"]["commandPreview"])
            self.assertIn("@example/mcp", by_name["secret-srv"]["commandPreview"])

            self.assertFalse(by_name["disabled-srv"]["enabled"])
            self.assertEqual(by_name["disabled-srv"]["estTokens"], 0)
            self.assertEqual(by_name["disabled-srv"]["envKeys"], ["SECRET_TOKEN"])

    def test_emits_duplicate_plugin_rule_heavy_skill_override_and_high_mcp_warnings(self) -> None:
        import lens_footprint as lf

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            ws = root / "ws"
            ws.mkdir(parents=True)

            # Duplicate plugins
            _write_json(
                home / ".gemini" / "config" / "plugins" / "agy-context-guard" / "plugin.json",
                {"name": "agy-context-guard", "version": "1.0.0"},
            )
            _write_json(
                home / ".gemini" / "config" / "plugins" / "agy-lens" / "plugin.json",
                {"name": "agy-lens", "version": "2.0.0"},
            )

            # Heavy rule (> 2000 est tokens => > 8000 bytes)
            _write_text(home / ".gemini" / "GEMINI.md", "A" * 8500)

            # Duplicate skill name across scopes
            _write_text(
                home / ".gemini" / "config" / "skills" / "shared-skill" / "SKILL.md",
                "---\nname: shared-skill\ndescription: Global version.\n---\nGlobal body",
            )
            _write_text(
                ws / ".agents" / "skills" / "shared-skill" / "SKILL.md",
                "---\nname: shared-skill\ndescription: Workspace override.\n---\nWorkspace body",
            )

            # > 8 enabled MCP servers (9 enabled, 1 disabled)
            mcp_servers = {
                f"srv-{i}": {"command": "python3", "args": [f"s{i}.py"]} for i in range(9)
            }
            mcp_servers["srv-off"] = {"disabled": True, "command": "python3", "args": ["off.py"]}
            _write_json(home / ".gemini" / "config" / "mcp_config.json", {"mcpServers": mcp_servers})

            with mock.patch.dict(os.environ, self._isolated_env(home), clear=True):
                result = lf.inspect_footprint(workspace_path=str(ws))

            warnings_by_code = {w["code"]: w for w in result["warnings"]}
            self.assertIn("duplicate_plugin", warnings_by_code)
            self.assertEqual(warnings_by_code["duplicate_plugin"]["severity"], "warning")
            self.assertIn("agy plugin uninstall agy-context-guard", warnings_by_code["duplicate_plugin"]["detail"])

            self.assertIn("rule_heavy", warnings_by_code)
            self.assertEqual(warnings_by_code["rule_heavy"]["severity"], "warning")

            self.assertIn("skill_override", warnings_by_code)
            self.assertEqual(warnings_by_code["skill_override"]["severity"], "info")

            self.assertIn("high_mcp_count", warnings_by_code)
            self.assertEqual(warnings_by_code["high_mcp_count"]["severity"], "warning")

    def test_handles_missing_directories_gracefully_and_resolves_workspace_fallbacks(self) -> None:
        import lens_footprint as lf

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "empty-home"
            home.mkdir(parents=True)
            ws_env = root / "env-ws"
            ws_env.mkdir(parents=True)

            # Empty home + explicit workspace that does not exist
            missing_ws = root / "does-not-exist"
            with mock.patch.dict(os.environ, self._isolated_env(home), clear=True):
                result = lf.inspect_footprint(workspace_path=str(missing_ws))

            self.assertEqual(result["rules"], [])
            self.assertEqual(result["plugins"], [])
            self.assertEqual(result["skills"], [])
            self.assertEqual(result["mcpServers"], [])
            self.assertEqual(result["hooks"], [])
            self.assertEqual(result["warnings"], [])
            self.assertEqual(
                result["builtinSkillsSummary"],
                {"count": 0, "frontmatterTokens": 0, "onDemandTokens": 0},
            )
            self.assertEqual(result["summary"]["estimatedStartupTokens"], 0)
            self.assertEqual(result["summary"]["onDemandSkillsTokens"], 0)
            self.assertIsNone(result["summary"]["observedStartupTokens"])

            # AGY_LENS_WORKSPACE fallback when workspace_path is omitted
            _write_text(ws_env / "AGENTS.md", "Env workspace rule.")
            env_with_ws = self._isolated_env(home, {"AGY_LENS_WORKSPACE": str(ws_env)})
            with mock.patch.dict(os.environ, env_with_ws, clear=True):
                env_result = lf.inspect_footprint()

            self.assertEqual(env_result["workspacePath"], str(ws_env.resolve()))
            self.assertEqual(len(env_result["rules"]), 1)

            # Active transcript cwd/workspacePaths hint fallback
            ws_tx = root / "tx-ws"
            ws_tx.mkdir(parents=True)
            _write_text(ws_tx / "GEMINI.md", "Transcript workspace rule.")
            tx_path = home / ".gemini" / "antigravity" / "brain" / "conv-ws" / ".system_generated" / "logs" / "transcript.jsonl"
            tx_path.parent.mkdir(parents=True, exist_ok=True)
            tx_path.write_text(json.dumps({"cwd": str(ws_tx)}) + "\n", encoding="utf-8")

            with mock.patch.dict(os.environ, self._isolated_env(home), clear=True):
                tx_result = lf.inspect_footprint()

            self.assertEqual(tx_result["workspacePath"], str(ws_tx.resolve()))
            self.assertEqual(len(tx_result["rules"]), 1)

    def test_respects_config_json_plugin_disabled_state_and_skills_json_entries(self) -> None:
        import lens_footprint as lf

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            ws = root / "ws"
            ws.mkdir(parents=True)

            # Two disabled plugins with overlapping skill names (should NOT warn or count),
            # plus one active plugin with only installedFrom metadata in config.json.
            for pid in ("chemistry", "structural-biology"):
                pdir = home / ".gemini" / "config" / "plugins" / pid
                _write_json(pdir / "plugin.json", {"name": pid, "version": "1.0.1"})
                for sname in ("credentials", "uv"):
                    _write_text(
                        pdir / "skills" / sname / "SKILL.md",
                        f"---\nname: {sname}\ndescription: Shared {pid} helper.\n---\nBody",
                    )
                _write_json(
                    pdir / "mcp_config.json",
                    {"mcpServers": {f"{pid}-mcp": {"command": "python3", "args": ["m.py"]}}},
                )

            notion_dir = home / ".gemini" / "config" / "plugins" / "notion"
            _write_json(notion_dir / "plugin.json", {"name": "notion", "version": "0.1.0"})
            _write_text(
                notion_dir / "skills" / "notion-setup" / "SKILL.md",
                "---\nname: notion-setup\ndescription: Notion setup.\n---\nBody",
            )

            _write_json(
                home / ".gemini" / "config" / "config.json",
                {
                    "plugins": {
                        "chemistry": {"enabled": False, "installedFrom": {"id": "official/chemistry"}},
                        "structural-biology": {"enabled": False, "installedFrom": {"id": "official/structural-biology"}},
                        "notion": {"installedFrom": {"id": "partners/notion"}},
                    },
                    "userSettings": {"theme": "dark"},
                },
            )

            # Custom skills declared via ~/.gemini/config/skills.json
            ext_skills = root / "agent-skills" / "skills"
            _write_text(
                ext_skills / "humanizer" / "SKILL.md",
                "---\nname: humanizer\ndescription: Rewrite prose cleanly.\n---\nRules here.",
            )
            _write_text(
                ext_skills / "skip-me" / "SKILL.md",
                "---\nname: skip-me\ndescription: Excluded skill.\n---\nBody",
            )
            _write_json(
                home / ".gemini" / "config" / "skills.json",
                {
                    "entries": [
                        {"path": str(ext_skills), "exclude": ["skip-.*"]},
                    ]
                },
            )

            with mock.patch.dict(os.environ, self._isolated_env(home), clear=True):
                result = lf.inspect_footprint(workspace_path=str(ws))

            plugins_by_id = {p["id"]: p for p in result["plugins"]}
            self.assertFalse(plugins_by_id["chemistry"]["enabled"])
            self.assertFalse(plugins_by_id["structural-biology"]["enabled"])
            self.assertTrue(plugins_by_id["notion"]["enabled"])

            skill_names = {s["name"] for s in result["skills"]}
            self.assertEqual(skill_names, {"notion-setup", "humanizer"})
            self.assertEqual(result["mcpServers"], [])
            self.assertFalse(any(w["code"] == "skill_override" for w in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
