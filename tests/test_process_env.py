import tempfile
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from process_env import load_env_keys


class ProcessEnvAllowlistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env_file = Path(self.tmp.name) / ".env"

    def test_returns_only_exact_allowed_keys(self):
        self.env_file.write_text(
            "OPENROUTER_API_KEY=synthetic-openrouter-key-123\n"
            "HERMES_TOKEN=synthetic-hermes-secret\n"
            "DATABASE_URL=postgres://synthetic:secret@localhost/db\n"
        )
        loaded = load_env_keys(self.env_file, allowed={"OPENROUTER_API_KEY"})
        self.assertEqual(loaded, {"OPENROUTER_API_KEY": "synthetic-openrouter-key-123"})
        self.assertNotIn("HERMES_TOKEN", loaded)
        self.assertNotIn("DATABASE_URL", loaded)

    def test_supports_wildcard_patterns_in_allowed(self):
        self.env_file.write_text(
            "MEETCAP_MODEL=large-v3-turbo\n"
            "MEETCAP_DEVICE=cuda\n"
            "MEETCAP_COMPUTE=float16\n"
            "OPENROUTER_API_KEY=synthetic-key\n"
            "SECRET_UNRELATED=synthetic-unrelated\n"
        )
        loaded = load_env_keys(self.env_file, allowed=["MEETCAP_*", "OPENROUTER_API_KEY"])
        self.assertEqual(
            loaded,
            {
                "MEETCAP_MODEL": "large-v3-turbo",
                "MEETCAP_DEVICE": "cuda",
                "MEETCAP_COMPUTE": "float16",
                "OPENROUTER_API_KEY": "synthetic-key",
            },
        )
        self.assertNotIn("SECRET_UNRELATED", loaded)

    def test_empty_allowed_returns_empty_dict(self):
        self.env_file.write_text("OPENROUTER_API_KEY=synthetic-key\n")
        self.assertEqual(load_env_keys(self.env_file, allowed=[]), {})
        self.assertEqual(load_env_keys(self.env_file, allowed=None), {})

    def test_case_sensitivity_enforced(self):
        self.env_file.write_text("openrouter_api_key=synthetic-lowercase\n")
        loaded = load_env_keys(self.env_file, allowed={"OPENROUTER_API_KEY"})
        self.assertEqual(loaded, {})


class ProcessEnvMissingAndInvalidFileTests(unittest.TestCase):
    def test_nonexistent_file_returns_empty_dict_without_raising(self):
        missing = Path("/path/to/definitely/nonexistent/.env")
        self.assertEqual(load_env_keys(missing, allowed={"OPENROUTER_API_KEY"}), {})

    def test_none_path_returns_empty_dict_without_raising(self):
        self.assertEqual(load_env_keys(None, allowed={"OPENROUTER_API_KEY"}), {})

    def test_directory_path_returns_empty_dict(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertEqual(load_env_keys(tmpdir, allowed={"OPENROUTER_API_KEY"}), {})


class ProcessEnvSyntaxAndParsingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env_file = Path(self.tmp.name) / ".env"

    def test_ignores_empty_lines_and_comments(self):
        self.env_file.write_text(
            "\n"
            "# Full line comment\n"
            "   # Indented comment\n"
            "\n"
            "OPENROUTER_API_KEY=synthetic-token-valid\n"
            "\n"
        )
        loaded = load_env_keys(self.env_file, allowed={"OPENROUTER_API_KEY"})
        self.assertEqual(loaded, {"OPENROUTER_API_KEY": "synthetic-token-valid"})

    def test_supports_export_prefix(self):
        self.env_file.write_text("export OPENROUTER_API_KEY=synthetic-export-key\n")
        loaded = load_env_keys(self.env_file, allowed={"OPENROUTER_API_KEY"})
        self.assertEqual(loaded, {"OPENROUTER_API_KEY": "synthetic-export-key"})

    def test_strips_double_and_single_quotes(self):
        self.env_file.write_text(
            'KEY_A="quoted value with spaces"\n'
            "KEY_B='single quoted value'\n"
            "KEY_C=unquoted_value\n"
        )
        loaded = load_env_keys(self.env_file, allowed=["KEY_*"])
        self.assertEqual(
            loaded,
            {
                "KEY_A": "quoted value with spaces",
                "KEY_B": "single quoted value",
                "KEY_C": "unquoted_value",
            },
        )

    def test_handles_inline_comments_and_hashes_in_quotes(self):
        self.env_file.write_text(
            "KEY_A=unquoted # inline comment\n"
            'KEY_B="value#with#hash" # comment after quote\n'
        )
        loaded = load_env_keys(self.env_file, allowed=["KEY_*"])
        self.assertEqual(
            loaded,
            {
                "KEY_A": "unquoted",
                "KEY_B": "value#with#hash",
            },
        )

    def test_handles_equal_sign_in_value(self):
        self.env_file.write_text("KEY_EQ=part1=part2=part3\n")
        loaded = load_env_keys(self.env_file, allowed={"KEY_EQ"})
        self.assertEqual(loaded, {"KEY_EQ": "part1=part2=part3"})

    def test_no_eval_or_command_execution(self):
        # Shell command substitution or subshell syntax must be treated as inert strings.
        self.env_file.write_text(
            "KEY_CMD=$(echo malicious)\n"
            "KEY_BACKTICK=`echo malicious`\n"
            "KEY_SEMI=val; rm -rf /tmp/fake\n"
        )
        loaded = load_env_keys(self.env_file, allowed=["KEY_*"])
        self.assertEqual(loaded["KEY_CMD"], "$(echo malicious)")
        self.assertEqual(loaded["KEY_BACKTICK"], "`echo malicious`")
        self.assertEqual(loaded["KEY_SEMI"], "val; rm -rf /tmp/fake")


if __name__ == "__main__":
    unittest.main()
