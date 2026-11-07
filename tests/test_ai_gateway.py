"""The OpenAI gateway and local configuration: no network, no real key. The key below is a made-up test string."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import openai

from grant_platform.services import ai, matching
from grant_platform.services.matching import GatewayError

FAKE_KEY = "sk-test-FAKE0123456789"


class EnvTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("OPENAI_API_KEY", "OPENAI_MODEL", "UNRELATED_SETTING"):
            os.environ.pop(name, None)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / ".env"

    def test_only_the_two_openai_settings_are_loaded(self):
        self.path.write_text(f"# comment\nOPENAI_API_KEY='{FAKE_KEY}'\nOPENAI_MODEL=gpt-test\nUNRELATED_SETTING=1\nBROKEN LINE\n", encoding="utf-8")
        ai.load_env(self.path)
        self.assertEqual(os.environ["OPENAI_API_KEY"], FAKE_KEY)
        self.assertEqual(ai.model_name(), "gpt-test")
        self.assertNotIn("UNRELATED_SETTING", os.environ)

    def test_existing_environment_wins_and_blank_values_are_ignored(self):
        os.environ["OPENAI_API_KEY"] = "from-environment"
        self.path.write_text("OPENAI_API_KEY=from-file\nOPENAI_MODEL=\n", encoding="utf-8")
        ai.load_env(self.path)
        self.assertEqual(os.environ["OPENAI_API_KEY"], "from-environment")
        self.assertEqual(ai.model_name(), matching.DEFAULT_MODEL)

    def test_missing_file_means_not_configured(self):
        ai.load_env(self.path)
        self.assertFalse(ai.configured())
        with patch.object(ai, "ENV_PATH", self.path):
            self.assertIsNone(ai.default_gateway())

    def test_default_gateway_exists_once_a_key_is_present(self):
        os.environ["OPENAI_API_KEY"] = FAKE_KEY
        gateway = ai.default_gateway()
        self.assertIsInstance(gateway, ai.OpenAIGateway)
        self.assertEqual(gateway.model, matching.DEFAULT_MODEL)
        self.assertNotIn(FAKE_KEY, repr(vars(gateway)))


class GatewayTests(unittest.TestCase):
    def call(self, client):
        with patch("openai.OpenAI", return_value=client):
            return ai.OpenAIGateway(model="gpt-test")("instructions", {"a": 1}, matching.ModelAnalysis)

    def client(self, parse):
        client = MagicMock()
        client.__enter__.return_value = client
        client.responses.parse = parse
        return client

    def test_a_completed_response_is_returned_and_sent_as_structured_output(self):
        parsed = object()
        parse = MagicMock(return_value=SimpleNamespace(status="completed", output_parsed=parsed))
        self.assertIs(self.call(self.client(parse)), parsed)
        arguments = parse.call_args.kwargs
        self.assertEqual((arguments["model"], arguments["instructions"], arguments["text_format"]), ("gpt-test", "instructions", matching.ModelAnalysis))
        self.assertEqual(arguments["input"], '{"a": 1}')

    def test_errors_are_reduced_to_a_message_without_credentials(self):
        error = openai.OpenAIError(f"Incorrect API key provided: {FAKE_KEY}")
        with self.assertRaises(GatewayError) as caught:
            self.call(self.client(MagicMock(side_effect=error)))
        self.assertNotIn(FAKE_KEY, str(caught.exception))
        self.assertNotIn(FAKE_KEY, repr(caught.exception.__cause__))
        self.assertEqual(str(caught.exception), "OpenAIError")

    def test_status_codes_are_kept_but_not_messages(self):
        error = openai.OpenAIError(f"quota used by {FAKE_KEY}")
        error.status_code = 429
        with self.assertRaises(GatewayError) as caught:
            self.call(self.client(MagicMock(side_effect=error)))
        self.assertEqual(str(caught.exception), "OpenAIError, HTTP 429")

    def test_an_incomplete_response_is_an_error(self):
        parse = MagicMock(return_value=SimpleNamespace(status="incomplete", output_parsed=None))
        with self.assertRaises(GatewayError):
            self.call(self.client(parse))

    def test_unparseable_output_is_an_error(self):
        try:
            matching.Criterion(score=9, reasoning="x")
        except matching.SchemaError as schema_error:
            failure = schema_error
        with self.assertRaises(GatewayError) as caught:
            self.call(self.client(MagicMock(side_effect=failure)))
        self.assertEqual(str(caught.exception), "answer not in the expected form")


class StructuredOutputSchemaTests(unittest.TestCase):
    """The API only accepts schemas where every property is required and extra properties are refused. The SDK
    converts our pydantic models offline, so a model that could not be sent is caught here, not in a live run."""

    def objects(self, node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                yield node
            for value in node.values():
                yield from self.objects(value)
        elif isinstance(node, list):
            for value in node:
                yield from self.objects(value)

    def test_both_answer_models_convert_to_strict_schemas(self):
        try:
            from openai.lib._parsing import type_to_response_format_param
        except ImportError:  # a different SDK layout: the live check will catch a bad schema
            self.skipTest("SDK helper not available")
        from grant_platform.services import drafting
        for model in (matching.ModelAnalysis, drafting.DraftAnalysis):
            response_format = type_to_response_format_param(model)
            self.assertTrue(response_format["json_schema"]["strict"])
            objects = list(self.objects(response_format["json_schema"]["schema"]))
            self.assertGreater(len(objects), 0)
            for node in objects:
                self.assertEqual(set(node["required"]), set(node["properties"]))
                self.assertIs(node["additionalProperties"], False)


if __name__ == "__main__":
    unittest.main()
