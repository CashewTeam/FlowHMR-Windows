"""All application languages keep their UI keys and interpolation variables in sync."""
import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


WEB = Path(__file__).resolve().parents[1] / "tools/web"
JS = WEB / "static/js"
LANGUAGES = {"zh-CN", "zh-TW", "en", "ja", "ko", "ru", "fr"}
PLACEHOLDERS = re.compile(r"\{(\w+)\}")


def _unique_catalog(pairs):
    result = {}
    for key, text in pairs:
        if key in result:
            raise ValueError(f"Duplicate translation key: {key}")
        result[key] = text
    return result


def read_catalog(path):
    text = path.read_text(encoding="utf-8")
    return json.loads(text.split("Object.freeze(", 1)[1].rsplit(");", 1)[0], object_pairs_hook=_unique_catalog)


class MarkupMessages(HTMLParser):
    def __init__(self):
        super().__init__()
        self.keys = set()

    def handle_starttag(self, _tag, attrs):
        self.keys.update(value for attr, value in attrs if attr == "data-i18n" or attr.startswith("data-i18n-"))


class LocalizationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogs = {path.stem: read_catalog(path) for path in (JS / "locales").glob("*.js")}
        cls.baseline = cls.catalogs["zh-CN"]

    def test_catalog_parity(self):
        self.assertEqual(set(self.catalogs), LANGUAGES)
        for locale, catalog in self.catalogs.items():
            with self.subTest(locale=locale):
                self.assertEqual(set(catalog), set(self.baseline))
                for key, text in catalog.items():
                    self.assertTrue(text.strip(), key)
                    self.assertEqual(set(PLACEHOLDERS.findall(text)), set(PLACEHOLDERS.findall(self.baseline[key])), key)

    def test_static_and_explicit_js_messages_exist(self):
        markup = MarkupMessages()
        markup.feed((WEB / "index.html").read_text(encoding="utf-8"))
        self.assertFalse(markup.keys - self.baseline.keys())
        literal_key = re.compile(r'\b(?:m|t|appError)\(\s*["\'](\w+[\w.-]*\.[\w.-]+)["\']')
        for source in JS.glob("*.js"):
            keys = set(literal_key.findall(source.read_text(encoding="utf-8")))
            with self.subTest(source=source.name):
                self.assertFalse(keys - self.baseline.keys())
