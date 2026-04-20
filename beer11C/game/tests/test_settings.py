from django.test import SimpleTestCase

from beer_game.settings import _normalize_host


class NormalizeHostTest(SimpleTestCase):
    def test_domain_and_port(self):
        self.assertEqual(_normalize_host('example.com:443'), 'example.com')

    def test_url(self):
        self.assertEqual(
            _normalize_host('https://beergame-aaqe.onrender.com'),
            'beergame-aaqe.onrender.com',
        )

    def test_wildcard_and_subdomain_pattern(self):
        self.assertEqual(_normalize_host('*'), '*')
        self.assertEqual(_normalize_host('.onrender.com'), '.onrender.com')

    def test_ipv6_bracketed_with_port(self):
        self.assertEqual(_normalize_host('[::1]:8000'), '::1')

    def test_ipv6_unbracketed(self):
        self.assertEqual(_normalize_host('2001:db8::1'), '2001:db8::1')

    def test_rejects_malformed(self):
        self.assertEqual(_normalize_host('://example.com'), '')
        self.assertEqual(_normalize_host('[::1'), '')
