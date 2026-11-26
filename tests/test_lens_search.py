from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import lens_search  # noqa: E402


def _write_jsonl(path: Path, rows: list[dict | str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for row in rows:
        if isinstance(row, str):
            lines.append(row)
        else:
            lines.append(json.dumps(row))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestLensSearch(unittest.TestCase):
    def _env_for(self, root: Path, cfg_dir: Path, cache_path: Path | None = None) -> dict[str, str]:
        env = {
            "AGY_LENS_CONFIG_PATH": str(cfg_dir / "agy-lens.json"),
            "AGY_LENS_BRAIN_ROOTS": str(root),
        }
        if cache_path is not None:
            env["AGY_LENS_SEARCH_CACHE_PATH"] = str(cache_path)
        return env

    def test_search_transcript_artifacts_and_files_with_spans(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "antigravity"
            cfg_dir = tmp_path / "config"
            cache_path = cfg_dir / "search-cache.json"

            conv_a = root / "brain" / "conv-alpha"
            _write_jsonl(
                conv_a / ".system_generated" / "logs" / "transcript.jsonl",
                [
                    {
                        "step_index": 0,
                        "source": "USER_EXPLICIT",
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-25T10:00:00Z",
                        "content": (
                            "<ADDITIONAL_METADATA>ignored metadata</ADDITIONAL_METADATA>\n"
                            "<USER_REQUEST>\n"
                            "Implement telemetry pipeline with resilient retry backoff across workers.\n"
                            "</USER_REQUEST>"
                        ),
                    },
                    {
                        "step_index": 1,
                        "source": "MODEL",
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "created_at": "2026-11-25T10:00:05Z",
                        "content": "Updating the worker module and documenting the architecture.",
                        "tool_calls": [
                            {"name": "read_file", "args": {"Path": "/repo/readonly_ignored.py"}},
                            {"name": "edit_file", "args": {"Path": "/repo/src/retry_worker.py"}},
                            {"name": "write_to_file", "args": {"TargetFile": "/repo/docs/notes.md"}},
                        ],
                    },
                    {
                        "step_index": 2,
                        "source": "SYSTEM",
                        "type": "GENERIC",
                        "status": "DONE",
                        "created_at": "2026-11-25T10:00:06Z",
                        "content": "Successfully updated /repo/src/retry_worker.py",
                    },
                ],
            )
            (conv_a / "task.md").write_text(
                "# Task Checklist\n- Verify jittered exponential backoff in worker loop.\n",
                encoding="utf-8",
            )
            sdd_dir = conv_a / "sdd"
            sdd_dir.mkdir(parents=True, exist_ok=True)
            (sdd_dir / "design.md").write_text(
                "Architecture note: " + ("context padding " * 12) + "telemetry collector checkpoint design.",
                encoding="utf-8",
            )
            # Hidden .system_generated markdown must never be indexed as an artifact.
            (conv_a / ".system_generated" / "secret_hidden.md").write_text(
                "hidden_only_marker_xyz should not appear",
                encoding="utf-8",
            )

            conv_b = root / "brain" / "conv-beta"
            _write_jsonl(
                conv_b / ".system_generated" / "logs" / "transcript.jsonl",
                [
                    {
                        "step_index": 0,
                        "source": "USER_EXPLICIT",
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "created_at": "2026-11-25T11:00:00Z",
                        "content": "Build telemetry dashboard UI only.",
                    },
                ],
            )

            with mock.patch.dict(os.environ, self._env_for(root, cfg_dir, cache_path), clear=False):
                # Multi-term AND across transcript ("telemetry") and sdd/design.md ("checkpoint")
                res = lens_search.search_conversations("telemetry checkpoint")
                self.assertEqual(res["query"], "telemetry checkpoint")
                self.assertEqual(res["scope"], "all")
                self.assertFalse(res["includeSubagents"])
                self.assertEqual(res["totalIndexed"], 2)
                self.assertEqual(res["matchedCount"], 1)
                self.assertEqual(len(res["results"]), 1)

                hit = res["results"][0]
                self.assertEqual(hit["convId"], "conv-alpha")
                self.assertEqual(hit["sourceApp"], "Antigravity")
                self.assertFalse(hit["isSubagent"])
                self.assertGreaterEqual(hit["matchCount"], 2)
                self.assertEqual(
                    hit["editedFiles"],
                    ["/repo/src/retry_worker.py", "/repo/docs/notes.md"],
                )
                self.assertNotIn("/repo/readonly_ignored.py", hit["editedFiles"])
                self.assertGreaterEqual(len(hit["snippets"]), 1)
                self.assertLessEqual(len(hit["snippets"]), 3)

                for snip in hit["snippets"]:
                    text = snip["text"]
                    spans = snip["spans"]
                    self.assertTrue(spans)
                    prev_end = 0
                    for start, end in spans:
                        self.assertGreaterEqual(start, prev_end)
                        self.assertLess(start, end)
                        self.assertLessEqual(end, len(text))
                        self.assertIn(text[start:end].lower(), {"telemetry", "checkpoint"})
                        prev_end = end

                # Hidden .system_generated markdown must not match
                hidden_res = lens_search.search_conversations("hidden_only_marker_xyz")
                self.assertEqual(hidden_res["matchedCount"], 0)

                # Scope filters
                files_hit = lens_search.search_conversations("retry_worker.py", scope="files")
                self.assertEqual(files_hit["scope"], "files")
                self.assertEqual(files_hit["matchedCount"], 1)
                self.assertEqual(files_hit["results"][0]["convId"], "conv-alpha")

                files_miss = lens_search.search_conversations("checkpoint", scope="files")
                self.assertEqual(files_miss["matchedCount"], 0)

                art_hit = lens_search.search_conversations("jittered backoff", scope="artifacts")
                self.assertEqual(art_hit["matchedCount"], 1)
                self.assertTrue(all(s["source"] == "artifact" for s in art_hit["results"][0]["snippets"]))

                art_miss = lens_search.search_conversations("resilient", scope="artifacts")
                self.assertEqual(art_miss["matchedCount"], 0)

                msg_miss = lens_search.search_conversations("jittered", scope="messages")
                self.assertEqual(msg_miss["matchedCount"], 0)

                # Invalid scope falls back to "all" and limit clamps cleanly
                clamped = lens_search.search_conversations("telemetry", scope="bogus", limit=999)
                self.assertEqual(clamped["scope"], "all")
                self.assertEqual(clamped["matchedCount"], 2)
                clamped_one = lens_search.search_conversations("telemetry", limit=0)
                self.assertEqual(len(clamped_one["results"]), 1)

    def test_search_quoted_phrase_and_subagent_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "antigravity"
            cfg_dir = tmp_path / "config"

            # Main chat with exact adjacent phrase
            _write_jsonl(
                root / "brain" / "conv-phrase" / ".system_generated" / "logs" / "transcript.jsonl",
                [
                    {
                        "step_index": 0,
                        "source": "USER_EXPLICIT",
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "content": "Design a token rate limiter for outbound requests.",
                    }
                ],
            )
            # Main chat with words separated (must NOT match quoted "rate limiter")
            _write_jsonl(
                root / "brain" / "conv-split" / ".system_generated" / "logs" / "transcript.jsonl",
                [
                    {
                        "step_index": 0,
                        "source": "USER_EXPLICIT",
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "content": "Check the error rate and also tune the request limiter.",
                    }
                ],
            )
            # Subagent chat with exact phrase
            _write_jsonl(
                root / "brain" / "conv-sub" / ".system_generated" / "logs" / "transcript.jsonl",
                [
                    {
                        "step_index": 0,
                        "source": "USER_EXPLICIT",
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "content": "Audit the token rate limiter edge cases.",
                    }
                ],
            )

            db_path = root / "conversation_summaries.db"
            con = sqlite3.connect(str(db_path))
            con.execute(
                "CREATE TABLE conversation_summaries ("
                "conversation_id TEXT PRIMARY KEY, title TEXT, preview TEXT, "
                "parent_conversation_id TEXT, agent_name TEXT)"
            )
            con.execute(
                "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?)",
                ("conv-sub", "Subagent Rate Limiter Audit", "", "conv-phrase", "reviewer"),
            )
            con.commit()
            con.close()

            with mock.patch.dict(os.environ, self._env_for(root, cfg_dir), clear=False):
                # Default excludes subagents
                res_default = lens_search.search_conversations('"rate limiter"')
                self.assertEqual([r["convId"] for r in res_default["results"]], ["conv-phrase"])

                # include_subagents=True includes conv-sub, and title hit (+20) ranks conv-sub first
                res_with_sub = lens_search.search_conversations('"rate limiter"', include_subagents=True)
                self.assertEqual(
                    [r["convId"] for r in res_with_sub["results"]],
                    ["conv-sub", "conv-phrase"],
                )
                self.assertTrue(res_with_sub["results"][0]["isSubagent"])
                self.assertFalse(res_with_sub["results"][1]["isSubagent"])

    def test_search_skips_corrupt_jsonl_and_strips_base64_blobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "antigravity"
            cfg_dir = tmp_path / "config"

            conv_dir = root / "brain" / "conv-noisy"
            b64_blob = "data:image/png;base64," + ("QUJDRA==" * 2000)
            giant_garbage = json.dumps(
                {
                    "step_index": 99,
                    "type": "GENERIC",
                    "content": "giant_skipped_token " + ("Z" * (210 * 1024)),
                }
            )
            _write_jsonl(
                conv_dir / ".system_generated" / "logs" / "transcript.jsonl",
                [
                    "{not valid json at all",
                    giant_garbage,
                    {
                        "step_index": 0,
                        "source": "USER_EXPLICIT",
                        "type": "USER_INPUT",
                        "status": "DONE",
                        "content": f"Screenshot attached {b64_blob} showing needle_anchor_token error.",
                    },
                ],
            )
            # Giant transcript_full.jsonl must never be read
            (conv_dir / ".system_generated" / "logs" / "transcript_full.jsonl").write_text(
                json.dumps({"type": "USER_INPUT", "content": "full_log_only_secret_marker"}) + "\n",
                encoding="utf-8",
            )
            # Oversized markdown > 512 KiB should not crash
            (conv_dir / "huge.md").write_text(
                "Header needle_anchor_token\n" + ("A" * (600 * 1024)),
                encoding="utf-8",
            )

            with mock.patch.dict(os.environ, self._env_for(root, cfg_dir), clear=False):
                res = lens_search.search_conversations("needle_anchor_token")
                self.assertEqual(res["matchedCount"], 1)
                snippets = res["results"][0]["snippets"]
                self.assertTrue(snippets)
                joined = " ".join(s["text"] for s in snippets)
                self.assertNotIn("QUJDRA==", joined)
                self.assertIn("[base64 omitted]", joined)

                # Giant line > 200KB and transcript_full.jsonl must not be indexed
                self.assertEqual(lens_search.search_conversations("giant_skipped_token")["matchedCount"], 0)
                self.assertEqual(lens_search.search_conversations("full_log_only_secret_marker")["matchedCount"], 0)

    def test_mtime_cache_reuses_unchanged_and_evicts_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "antigravity"
            cfg_dir = tmp_path / "config"
            cache_path = cfg_dir / "custom-cache.json"

            conv_keep = root / "brain" / "conv-keep"
            _write_jsonl(
                conv_keep / ".system_generated" / "logs" / "transcript.jsonl",
                [{"step_index": 0, "type": "USER_INPUT", "content": "Alpha keeper workflow"}],
            )
            conv_drop = root / "brain" / "conv-drop"
            _write_jsonl(
                conv_drop / ".system_generated" / "logs" / "transcript.jsonl",
                [{"step_index": 0, "type": "USER_INPUT", "content": "Temporary deleted workflow"}],
            )

            with mock.patch.dict(os.environ, self._env_for(root, cfg_dir, cache_path), clear=False):
                self.assertEqual(lens_search.get_search_cache_path(), cache_path)

                with mock.patch.object(
                    lens_search,
                    "_index_conversation",
                    wraps=lens_search._index_conversation,
                ) as index_spy:
                    first = lens_search.search_conversations("workflow")
                    self.assertEqual(first["matchedCount"], 2)
                    self.assertEqual(index_spy.call_count, 2)

                    # Second search with unchanged files: zero re-indexing calls!
                    second = lens_search.search_conversations("Alpha")
                    self.assertEqual(second["matchedCount"], 1)
                    self.assertEqual(index_spy.call_count, 2)

                    # Delete conv-drop and update conv-keep artifact
                    for p in sorted(conv_drop.rglob("*"), reverse=True):
                        if p.is_file():
                            p.unlink()
                        elif p.is_dir():
                            p.rmdir()
                    conv_drop.rmdir()

                    (conv_keep / "notes.md").write_text("Updated artifact notes", encoding="utf-8")

                    third = lens_search.search_conversations("Updated")
                    self.assertEqual(third["matchedCount"], 1)
                    self.assertEqual(index_spy.call_count, 3)

                saved = json.loads(cache_path.read_text(encoding="utf-8"))
                self.assertIn("conv-keep", saved["conversations"])
                self.assertNotIn("conv-drop", saved["conversations"])

            # Default cache path falls back alongside config path when env var is unset
            with mock.patch.dict(
                os.environ,
                {"AGY_LENS_CONFIG_PATH": str(cfg_dir / "agy-lens.json")},
                clear=False,
            ):
                os.environ.pop("AGY_LENS_SEARCH_CACHE_PATH", None)
                self.assertEqual(
                    lens_search.get_search_cache_path(),
                    cfg_dir / "agy-lens-search-cache.json",
                )

    def test_empty_query_returns_recent_conversations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "antigravity"
            cfg_dir = tmp_path / "config"

            older = root / "brain" / "conv-older"
            older_t = older / ".system_generated" / "logs" / "transcript.jsonl"
            _write_jsonl(
                older_t,
                [
                    {"step_index": 0, "type": "USER_INPUT", "content": "Older conversation prompt " + ("x" * 180)},
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "status": "DONE",
                        "tool_calls": [{"name": "write_file", "args": {"Path": "/repo/old.py"}}],
                    },
                ],
            )
            (older / "task.md").write_text("# Old Task\nDetails here.", encoding="utf-8")

            newer = root / "brain" / "conv-newer"
            newer_t = newer / ".system_generated" / "logs" / "transcript.jsonl"
            _write_jsonl(
                newer_t,
                [{"step_index": 0, "type": "USER_INPUT", "content": "Newer conversation prompt"}],
            )

            os.utime(older_t, (1_700_000_000, 1_700_000_000))
            os.utime(older / "task.md", (1_700_000_000, 1_700_000_000))
            os.utime(newer_t, (1_700_001_000, 1_700_001_000))

            with mock.patch.dict(os.environ, self._env_for(root, cfg_dir), clear=False):
                res = lens_search.search_conversations("   ")
                self.assertEqual(res["totalIndexed"], 2)
                self.assertEqual(res["matchedCount"], 2)
                self.assertEqual([r["convId"] for r in res["results"]], ["conv-newer", "conv-older"])

                older_entry = res["results"][1]
                self.assertEqual(older_entry["matchCount"], 0)
                self.assertEqual(older_entry["editedFiles"], ["/repo/old.py"])
                self.assertLessEqual(len(older_entry["snippets"]), 2)
                self.assertGreaterEqual(len(older_entry["snippets"]), 1)
                for snip in older_entry["snippets"]:
                    self.assertEqual(snip["spans"], [])
                    self.assertLessEqual(len(snip["text"]), 145)


if __name__ == "__main__":
    unittest.main()
