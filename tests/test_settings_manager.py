"""Tests for SettingsManager validation with Pydantic schema"""

import unittest
from config.settings_schema import AppSettings, LLMStudioSettings, ClineSettings
from pydantic import ValidationError


class TestSettingsSchema(unittest.TestCase):

    def setUp(self):
        self.valid_settings = {
            "connector_type": "lmstudio",
            "lmstudio": {
                "base_url": "http://localhost:1234",
                "model": "local-model",
                "temperature": 0.7,
                "max_tokens": 50000,
            },
            "cline": {
                "api_key": "",
                "model": "claude-sonnet-4-5",
                "temperature": 0.7,
                "max_tokens": 50000,
            },
        }

    def test_valid_settings_load(self):
        settings = AppSettings(**self.valid_settings)
        self.assertEqual(settings.connector_type, "lmstudio")
        self.assertEqual(settings.lmstudio.base_url, "http://localhost:1234")
        self.assertEqual(settings.cline.model, "claude-sonnet-4-5")

    def test_defaults_applied(self):
        minimal = {
            "connector_type": "lmstudio",
            "lmstudio": {
                "base_url": "http://localhost:1234",
                "model": "local-model",
            },
        }
        settings = AppSettings(**minimal)
        self.assertEqual(settings.lmstudio.temperature, 0.7)
        self.assertEqual(settings.lmstudio.max_tokens, 50000)

    def test_missing_connector_type(self):
        settings = self.valid_settings.copy()
        del settings["connector_type"]
        with self.assertRaises(ValidationError):
            AppSettings(**settings)

    def test_invalid_connector_type(self):
        settings = self.valid_settings.copy()
        settings["connector_type"] = "invalid"
        with self.assertRaises(ValidationError):
            AppSettings(**settings)

    def test_missing_lmstudio_section(self):
        settings = self.valid_settings.copy()
        del settings["lmstudio"]
        with self.assertRaises(ValidationError):
            AppSettings(**settings)

    def test_missing_lmstudio_base_url(self):
        settings = self.valid_settings.copy()
        del settings["lmstudio"]["base_url"]
        with self.assertRaises(ValidationError):
            AppSettings(**settings)

    def test_missing_lmstudio_model(self):
        settings = self.valid_settings.copy()
        del settings["lmstudio"]["model"]
        with self.assertRaises(ValidationError):
            AppSettings(**settings)

    def test_empty_lmstudio_base_url(self):
        settings = self.valid_settings.copy()
        settings["lmstudio"]["base_url"] = ""
        with self.assertRaises(ValidationError):
            AppSettings(**settings)

    def test_empty_lmstudio_model(self):
        settings = self.valid_settings.copy()
        settings["lmstudio"]["model"] = ""
        with self.assertRaises(ValidationError):
            AppSettings(**settings)

    def test_temperature_below_zero(self):
        settings = self.valid_settings.copy()
        settings["lmstudio"]["temperature"] = -0.1
        with self.assertRaises(ValidationError):
            AppSettings(**settings)

    def test_temperature_above_two(self):
        settings = self.valid_settings.copy()
        settings["lmstudio"]["temperature"] = 2.1
        with self.assertRaises(ValidationError):
            AppSettings(**settings)

    def test_temperature_boundary_zero(self):
        settings = self.valid_settings.copy()
        settings["lmstudio"]["temperature"] = 0
        result = AppSettings(**settings)
        self.assertEqual(result.lmstudio.temperature, 0)

    def test_temperature_boundary_two(self):
        settings = self.valid_settings.copy()
        settings["lmstudio"]["temperature"] = 2
        result = AppSettings(**settings)
        self.assertEqual(result.lmstudio.temperature, 2)

    def test_max_tokens_zero(self):
        settings = self.valid_settings.copy()
        settings["lmstudio"]["max_tokens"] = 0
        with self.assertRaises(ValidationError):
            AppSettings(**settings)

    def test_max_tokens_negative(self):
        settings = self.valid_settings.copy()
        settings["lmstudio"]["max_tokens"] = -1
        with self.assertRaises(ValidationError):
            AppSettings(**settings)

    def test_missing_cline_api_key(self):
        settings = self.valid_settings.copy()
        del settings["cline"]["api_key"]
        with self.assertRaises(ValidationError):
            AppSettings(**settings)

    def test_missing_cline_model(self):
        settings = self.valid_settings.copy()
        del settings["cline"]["model"]
        with self.assertRaises(ValidationError):
            AppSettings(**settings)

    def test_empty_cline_model(self):
        settings = self.valid_settings.copy()
        settings["cline"]["model"] = ""
        with self.assertRaises(ValidationError):
            AppSettings(**settings)

    def test_cline_temperature_out_of_range(self):
        settings = self.valid_settings.copy()
        settings["cline"]["temperature"] = 3.0
        with self.assertRaises(ValidationError):
            AppSettings(**settings)

    def test_cline_max_tokens_zero(self):
        settings = self.valid_settings.copy()
        settings["cline"]["max_tokens"] = 0
        with self.assertRaises(ValidationError):
            AppSettings(**settings)

    def test_cline_optional(self):
        settings = {
            "connector_type": "lmstudio",
            "lmstudio": {
                "base_url": "http://localhost:1234",
                "model": "local-model",
            },
        }
        result = AppSettings(**settings)
        self.assertIsNone(result.cline)

    def test_cline_connector_type(self):
        settings = self.valid_settings.copy()
        settings["connector_type"] = "cline"
        result = AppSettings(**settings)
        self.assertEqual(result.connector_type, "cline")

    def test_extra_fields_ignored_by_default(self):
        settings = self.valid_settings.copy()
        settings["unknown_field"] = "value"
        result = AppSettings(**settings)
        self.assertFalse(hasattr(result, "unknown_field"))


if __name__ == "__main__":
    unittest.main()