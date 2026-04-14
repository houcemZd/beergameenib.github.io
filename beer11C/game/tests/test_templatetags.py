"""
Tests for game.templatetags.game_extras — custom template filters.
"""
from django.test import TestCase
from game.templatetags.game_extras import get_item


class GetItemFilterTest(TestCase):
    def test_get_existing_key(self):
        d = {'a': 1, 'b': 2}
        self.assertEqual(get_item(d, 'a'), 1)

    def test_get_missing_key(self):
        d = {'a': 1}
        self.assertIsNone(get_item(d, 'x'))

    def test_none_dictionary(self):
        self.assertIsNone(get_item(None, 'x'))

    def test_empty_dictionary(self):
        self.assertIsNone(get_item({}, 'key'))
