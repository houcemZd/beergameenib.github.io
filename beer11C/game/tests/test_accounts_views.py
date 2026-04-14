"""
Tests for game.accounts_views — Login, Register, Logout.
"""
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse


class LoginViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            'alice', password='securepw123', first_name='Alice',
        )

    def test_login_get(self):
        resp = self.client.get(reverse('login'))
        self.assertEqual(resp.status_code, 200)

    def test_login_success(self):
        resp = self.client.post(reverse('login'), {
            'username': 'alice', 'password': 'securepw123',
        })
        self.assertEqual(resp.status_code, 302)

    def test_login_failure(self):
        resp = self.client.post(reverse('login'), {
            'username': 'alice', 'password': 'wrong',
        })
        self.assertEqual(resp.status_code, 200)  # re-renders the form

    def test_login_redirect_when_authenticated(self):
        self.client.login(username='alice', password='securepw123')
        resp = self.client.get(reverse('login'))
        self.assertEqual(resp.status_code, 302)

    def test_login_with_next(self):
        resp = self.client.post(reverse('login') + '?next=/new/', {
            'username': 'alice', 'password': 'securepw123',
        })
        self.assertEqual(resp.status_code, 302)


class RegisterViewTest(TestCase):
    def setUp(self):
        self.client = Client()

    def test_register_get(self):
        resp = self.client.get(reverse('register'))
        self.assertEqual(resp.status_code, 200)

    def test_register_success(self):
        resp = self.client.post(reverse('register'), {
            'username': 'bob',
            'email': 'bob@example.com',
            'password1': 'strongpw12345',
            'password2': 'strongpw12345',
            'first_name': 'Bob',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(User.objects.filter(username='bob').exists())

    def test_register_password_mismatch(self):
        resp = self.client.post(reverse('register'), {
            'username': 'bob',
            'email': 'bob@example.com',
            'password1': 'strongpw12345',
            'password2': 'different12345',
        })
        self.assertEqual(resp.status_code, 200)  # re-renders form with error

    def test_register_short_password(self):
        resp = self.client.post(reverse('register'), {
            'username': 'bob',
            'password1': 'short',
            'password2': 'short',
        })
        self.assertEqual(resp.status_code, 200)

    def test_register_duplicate_username(self):
        User.objects.create_user('bob', password='pw12345678')
        resp = self.client.post(reverse('register'), {
            'username': 'bob',
            'password1': 'strongpw12345',
            'password2': 'strongpw12345',
        })
        self.assertEqual(resp.status_code, 200)

    def test_register_redirect_when_authenticated(self):
        User.objects.create_user('alice', password='pw12345678')
        self.client.login(username='alice', password='pw12345678')
        resp = self.client.get(reverse('register'))
        self.assertEqual(resp.status_code, 302)

    def test_register_empty_username(self):
        resp = self.client.post(reverse('register'), {
            'username': '',
            'password1': 'strongpw12345',
            'password2': 'strongpw12345',
        })
        self.assertEqual(resp.status_code, 200)


class LogoutViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw12345678')
        self.client = Client()
        self.client.login(username='alice', password='pw12345678')

    def test_logout_post(self):
        resp = self.client.post(reverse('logout'))
        self.assertEqual(resp.status_code, 302)
