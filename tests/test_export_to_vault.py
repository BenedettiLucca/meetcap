import unittest

import export_to_vault


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
            export_to_vault.extract_pending_daily_tasks(note),
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
            export_to_vault.compute_explicit_task_matches(pending_tasks, summary, transcript),
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

        rendered = export_to_vault.render_task_suggestions(payload)

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

        normalized = export_to_vault.normalize_task_suggestion_payload(payload)

        self.assertEqual(normalized["matched_tasks"], ["- [ ] Bio WeeSearch"])
        self.assertEqual(normalized["new_suggested_tasks"], [])
        self.assertEqual(
            normalized["not_now_items"],
            [{"item": "Live diária", "reason": "é do Aaron"}],
        )


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

        content = export_to_vault.build_note_content(
            data=data,
            title="Meeting — 2026-05-20 (29 min)",
            summary="## 📌 Summary\nResumo\n",
            task_suggestions="## 🧩 Sugestões de Tarefas\n- [ ] Nova task\n",
        )

        self.assertLess(content.index("## 🧩 Sugestões de Tarefas"), content.index("## 📝 Transcrição Completa"))
        self.assertIn("- [ ] Nova task", content)


if __name__ == "__main__":
    unittest.main()
