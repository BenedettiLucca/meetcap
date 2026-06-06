import unittest
import sys
from pathlib import Path
from unittest.mock import patch

# Add src to path so we can import exporter
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from exporter import task_extractor
from exporter import transcript_parser
from exporter import summarizer
from exporter import vault_exporter


class DailyTaskExtractionTests(unittest.TestCase):
    def test_extract_pending_daily_tasks_only_reads_tasks_do_dia_pending(self):
        note = """# Tasks — 20/05/2026

## 🔁 Recorrentes
- [ ] Academia
- [x] Rezar

## 📋 Tasks do Dia
- [ ] 🔄 lavar a louça
- [x] tarefa já feita
- [ ] Bio WeeSearch

## ✅ Concluídas
- [x] Pedidos de design - INTMAX ✅ 2026-05-20
"""

        self.assertEqual(
            task_extractor.extract_pending_daily_tasks(note),
            ["- [ ] 🔄 lavar a louça", "- [ ] Bio WeeSearch"],
        )

    def test_compute_explicit_task_matches_is_conservative(self):
        pending_tasks = [
            "- [ ] Ads HC",
            "- [ ] Bio WeeSearch",
            "- [ ] lavar a louça",
        ]
        summary = "We discussed generic ads, audience growth, and site traffic."
        transcript = "Bio WeeSearch will be updated tomorrow."

        self.assertEqual(
            task_extractor.compute_explicit_task_matches(pending_tasks, summary, transcript),
            ["- [ ] Bio WeeSearch"],
        )


class TaskSuggestionRenderingTests(unittest.TestCase):
    def test_render_task_suggestions_formats_copy_paste_block(self):
        payload = {
            "matched_tasks": ["- [ ] Bio WeeSearch"],
            "new_suggested_tasks": ["- [ ] Definir CTA dos posts para levar tráfego ao site"],
            "not_now_items": [
                {
                    "item": "Esperar resultado dos ads",
                    "reason": "monitoramento, não ação imediata",
                }
            ],
        }

        rendered = task_extractor.render_task_suggestions(payload)

        self.assertIn("## 🧩 Sugestões de Tarefas", rendered)
        self.assertIn("### 🆕 Novas tarefas sugeridas pela reunião", rendered)
        self.assertIn("- [ ] Definir CTA dos posts para levar tráfego ao site", rendered)
        self.assertIn("- Esperar resultado dos ads — monitoramento, não ação imediata", rendered)

    def test_normalize_task_suggestion_payload_coerces_string_and_null_shapes(self):
        payload = {
            "matched_tasks": "- [ ] Bio WeeSearch",
            "new_suggested_tasks": None,
            "not_now_items": {"item": "Live diária", "reason": "é do Aaron"},
        }

        normalized = task_extractor.normalize_task_suggestion_payload(payload)

        self.assertEqual(normalized["matched_tasks"], ["- [ ] Bio WeeSearch"])
        self.assertEqual(normalized["new_suggested_tasks"], [])
        self.assertEqual(
            normalized["not_now_items"],
            [{"item": "Live diária", "reason": "é do Aaron"}],
        )


class LongMeetingSummaryTests(unittest.TestCase):
    def test_split_transcript_into_chunks_preserves_order_and_respects_limit(self):
        transcript = "\n".join(
            [
                "[00:00 → 00:05] alpha alpha alpha",
                "[00:05 → 00:10] beta beta beta",
                "[00:10 → 00:15] gamma gamma gamma",
                "[00:15 → 00:20] delta delta delta",
            ]
        )

        chunks = transcript_parser.split_transcript_into_chunks(transcript, max_chars=70)

        self.assertGreater(len(chunks), 1)
        self.assertEqual("\n".join(line for chunk in chunks for line in chunk.splitlines()), transcript)
        self.assertTrue(all(len(chunk) <= 70 for chunk in chunks))

    @patch("exporter.summarizer.call_openrouter")
    def test_reduce_chunk_summaries_merges_in_multiple_rounds_when_needed(self, mock_call_openrouter):
        mock_call_openrouter.side_effect = ["merged-ab", "merged-c", "final-summary"]

        result = summarizer.reduce_chunk_summaries(
            ["A" * 120, "B" * 120, "C" * 120],
            merge_max_chars=330,
        )

        self.assertEqual(result, "final-summary")
        self.assertEqual(mock_call_openrouter.call_count, 3)

    @patch("exporter.summarizer.call_openrouter")
    def test_generate_summary_uses_chunk_pipeline_for_long_transcripts(self, mock_call_openrouter):
        transcript = "\n".join(
            [
                "[00:00 → 00:05] alpha alpha alpha alpha",
                "[00:05 → 00:10] beta beta beta beta",
                "[00:10 → 00:15] gamma gamma gamma gamma",
                "[00:15 → 00:20] delta delta delta delta",
            ]
        )
        chunk_summary = (
            "## 📌 Summary\nChunk summary\n\n"
            "## 🔑 Key Points\n- Point\n\n"
            "## ✅ Action Items\n- [ ] Action\n\n"
            "## ⚠️ Open Questions / Risks\n- Risk"
        )
        final_summary = (
            "## 📌 Summary\nFinal summary\n\n"
            "## 🔑 Key Points\n- Final point\n\n"
            "## ✅ Action Items\n- [ ] Final action\n\n"
            "## ⚠️ Open Questions / Risks\n- Final risk"
        )
        mock_call_openrouter.side_effect = [chunk_summary, chunk_summary, final_summary]

        result = summarizer.generate_summary(
            transcript,
            single_pass_max_chars=80,
            chunk_max_chars=90,
            merge_max_chars=800,
        )

        self.assertEqual(result, final_summary)
        self.assertEqual(mock_call_openrouter.call_count, 3)
        first_user_prompt = mock_call_openrouter.call_args_list[0].kwargs["messages"][1]["content"]
        final_user_prompt = mock_call_openrouter.call_args_list[-1].kwargs["messages"][1]["content"]
        self.assertIn("Chunk 1 of 2", first_user_prompt)
        self.assertIn("chunk summaries", final_user_prompt.lower())


class NoteRenderingTests(unittest.TestCase):
    def test_build_note_content_includes_task_suggestions_before_transcript(self):
        data = {
            "meeting_date": "2026-05-20",
            "meeting_time": "16:03",
            "duration_str": "29 min",
            "line_count": 123,
            "transcript_text": "linha 1\nlinha 2",
            "meta": {
                "file": "meeting-2026-05-20_16-03-00.wav",
                "model": "large-v3-turbo (cuda/float16)",
                "language": "pt (99.9%)",
            },
        }

        content = vault_exporter.build_note_content(
            data=data,
            title="Meeting — 2026-05-20 (29 min)",
            summary="## 📌 Summary\nResumo\n",
            task_suggestions="## 🧩 Sugestões de Tarefas\n- [ ] Nova task\n",
        )

        self.assertLess(content.index("## 🧩 Sugestões de Tarefas"), content.index("## 📝 Transcrição Completa"))
        self.assertIn("- [ ] Nova task", content)


if __name__ == "__main__":
    unittest.main()
