import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from exporter.entity_resolver import (
    apply_corrections,
    extract_candidates,
    load_vocabulary,
    normalize_term,
    render_name_corrections,
    resolve_derived_surfaces,
    resolve_entities,
    score_candidate,
    slug_to_display,
)


def make_vault(root: Path) -> Path:
    (root / "wiki" / "entities").mkdir(parents=True)
    (root / "wiki" / "concepts").mkdir(parents=True)
    (root / "wiki" / "entities").joinpath("zcash.md").write_text("# Zcash\n", encoding="utf-8")
    (root / "wiki" / "entities").joinpath("project-tachyon.md").write_text("# T\n", encoding="utf-8")
    (root / "wiki" / "concepts").joinpath("agent-harness.md").write_text("# A\n", encoding="utf-8")
    return root


class NormalizeTests(unittest.TestCase):
    def test_lowers_and_deaccents(self):
        self.assertEqual(normalize_term("São Paulo!"), "sao paulo")


class SlugDisplayTests(unittest.TestCase):
    def test_slug_to_words(self):
        self.assertEqual(slug_to_display("project-tachyon.md"), "Project Tachyon")


class LoadVocabularyTests(unittest.TestCase):
    def test_loads_from_wiki_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            vocab = load_vocabulary(vault_root=make_vault(Path(tmp)))
        canonical = {normalize_term(name) for name in vocab["canonical"]}
        self.assertIn("zcash", canonical)
        self.assertIn("project tachyon", canonical)
        self.assertIn("agent harness", canonical)

    def test_missing_dirs_are_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            vocab = load_vocabulary(vault_root=Path(tmp) / "nowhere")
        self.assertEqual(vocab["canonical"], [])
        self.assertEqual(vocab["aliases"], {})

    def test_txt_glossary_terms_and_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_vault(Path(tmp))
            glossary = root / "docs" / "glossary.txt"
            glossary.parent.mkdir(parents=True)
            glossary.write_text(
                "TechKeyon = Project Tachyon\nMOIC\n# a comment\n\n", encoding="utf-8"
            )
            vocab = load_vocabulary(vault_root=root)
        canonical = {normalize_term(name) for name in vocab["canonical"]}
        self.assertIn("moic", canonical)
        self.assertEqual(vocab["aliases"].get("techkeyon"), "Project Tachyon")
        self.assertIn(normalize_term("Project Tachyon"), canonical)

    def test_json_glossary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_vault(Path(tmp))
            glossary = root / "docs" / "glossary.json"
            glossary.parent.mkdir(parents=True)
            glossary.write_text(
                '{"terms": ["Zcash"], "aliases": {"Jiracash": "Zcash"}}', encoding="utf-8"
            )
            vocab = load_vocabulary(vault_root=root)
        self.assertEqual(vocab["aliases"].get("jiracash"), "Zcash")

    def test_malformed_json_glossary_is_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_vault(Path(tmp))
            glossary = root / "docs" / "glossary.json"
            glossary.parent.mkdir(parents=True)
            glossary.write_text("{broken json", encoding="utf-8")
            vocab = load_vocabulary(vault_root=root)
        self.assertEqual(vocab["aliases"], {})

    def test_participants_registered(self):
        vocab = load_vocabulary(vault_root="/nonexistent", participants=["Maria Silva"])
        self.assertIn("maria silva", {normalize_term(n) for n in vocab["canonical"]})

    def test_explicit_glossary_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            glossary = Path(tmp) / "g.txt"
            glossary.write_text("Foo Bar\n", encoding="utf-8")
            vocab = load_vocabulary(vault_root="/nonexistent", glossary_paths=[glossary])
        self.assertIn("foo bar", {normalize_term(n) for n in vocab["canonical"]})


class ExtractCandidatesTests(unittest.TestCase):
    def test_finds_capitalized_surfaces(self):
        candidates = extract_candidates("Discussed Jiracash and Project Tachyon today.")
        self.assertIn("Discussed Jiracash", candidates)  # sentence-start merge handled downstream
        self.assertIn("Project Tachyon", candidates)

    def test_sentence_start_stopwords_ignored(self):
        candidates = extract_candidates("The meeting went fine. We agreed on The Tachyon plan.")
        self.assertNotIn("The meeting", candidates)
        self.assertIn("The Tachyon", candidates)

    def test_short_surfaces_ignored(self):
        self.assertNotIn("AI", extract_candidates("AI is here"))


class ScoreCandidateTests(unittest.TestCase):
    def setUp(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.vocab = load_vocabulary(vault_root=make_vault(Path(tmp)))
            self.vocab["aliases"]["techkeyon"] = "Project Tachyon"

    def test_canonical_surface_is_none(self):
        self.assertIsNone(score_candidate("Zcash", self.vocab))
        self.assertIsNone(score_candidate("Project Tachyon", self.vocab))

    def test_alias_is_exact(self):
        confidence, rule, canonical = score_candidate("TechKeyon", self.vocab)
        self.assertEqual((confidence, rule, canonical), (1.0, "alias", "Project Tachyon"))

    def test_fuzzy_high_confidence(self):
        confidence, rule, canonical = score_candidate("Zcasch", self.vocab)
        self.assertGreaterEqual(confidence, 0.87)
        self.assertEqual(canonical, "Zcash")
        self.assertEqual(rule, "fuzzy")

    def test_below_threshold_is_none(self):
        self.assertIsNone(score_candidate("Meeting", self.vocab))
        self.assertIsNone(score_candidate("Completely Different Thing", self.vocab))

    def test_substring_variant(self):
        confidence, rule, canonical = score_candidate("Tachyon", self.vocab)
        self.assertEqual(canonical, "Project Tachyon")
        self.assertGreaterEqual(confidence, 0.9)


class ResolveAndApplyTests(unittest.TestCase):
    def setUp(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.vocab = load_vocabulary(vault_root=make_vault(Path(tmp)))
            self.vocab["aliases"]["techkeyon"] = "Project Tachyon"
            self.vocab["aliases"]["jiracash"] = "Zcash"

    def test_high_confidence_applied(self):
        text = "We talked about TechKeyon and Jiracash timelines."
        corrected, corrections = resolve_derived_surfaces(text, self.vocab)
        self.assertIn("Project Tachyon", corrected)
        self.assertIn("Zcash", corrected)
        self.assertNotIn("TechKeyon", corrected)
        self.assertEqual(len(corrections), 2)
        self.assertTrue(all(c["auto_applied"] for c in corrections))

    def test_medium_confidence_flagged_not_applied(self):
        text = "Zcshh roadmap was discussed."
        corrected, corrections = resolve_derived_surfaces(text, self.vocab)
        self.assertEqual(len(corrections), 1)
        self.assertEqual(corrections[0]["canonical"], "Zcash")
        self.assertFalse(corrections[0]["auto_applied"])
        self.assertEqual(corrected, text)

    def test_sentence_start_word_does_not_block_match(self):
        text = "Discussed Jiracash timelines."
        corrected, corrections = resolve_derived_surfaces(text, self.vocab)
        self.assertIn("Zcash", corrected)
        self.assertEqual(corrections[0]["surface"], "Jiracash")

    def test_apply_respects_word_boundaries(self):
        corrections = [{"surface": "Zcash", "canonical": "Zcash Labs", "auto_applied": True}]
        self.assertEqual(apply_corrections("Zcash and Zcashly", corrections), "Zcash Labs and Zcashly")

    def test_no_corrections_on_clean_text(self):
        corrected, corrections = resolve_derived_surfaces("Plain text with no names.", self.vocab)
        self.assertEqual(corrected, "Plain text with no names.")
        self.assertEqual(corrections, [])


class RenderCorrectionsTests(unittest.TestCase):
    def test_empty_renders_nothing(self):
        self.assertEqual(render_name_corrections([]), "")

    def test_renders_audit_lines(self):
        block = render_name_corrections([
            {"surface": "TechKeyon", "canonical": "Project Tachyon",
             "confidence": 1.0, "rule": "alias", "auto_applied": True},
            {"surface": "Zcasch", "canonical": "Zcash",
             "confidence": 0.78, "rule": "fuzzy", "auto_applied": False},
        ])
        self.assertIn("## Name Corrections", block)
        self.assertIn("`TechKeyon` -> `Project Tachyon` (high confidence, alias)", block)
        self.assertIn("`Zcasch` -> `Zcash` (flagged, not rewritten, fuzzy)", block)


if __name__ == "__main__":
    unittest.main()
