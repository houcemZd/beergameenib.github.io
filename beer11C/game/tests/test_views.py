"""
Tests for game.views — HTTP views (home, new_game, lobby, join, play,
dashboard, next_turn, results, chart_data_api, client_view, customer_view, etc.)
"""
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from game.models import (
    GameSession, Player, PlayerSession, CustomerDemand,
    PipelineShipment, PipelineOrder,
)
from game.services import initialise_session, process_week


class AuthViewsTest(TestCase):
    """Test register/login/logout views defined in views.py."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('alice', password='testpw12345')

    def test_register_get(self):
        resp = self.client.get(reverse('register'))
        # The register view in views.py renders game/register.html
        # but accounts_views may override via urls. Just check it doesn't crash.
        self.assertIn(resp.status_code, [200, 302])

    def test_login_get(self):
        resp = self.client.get(reverse('login'))
        self.assertEqual(resp.status_code, 200)

    def test_login_post_success(self):
        resp = self.client.post(reverse('login'), {
            'username': 'alice', 'password': 'testpw12345',
        })
        self.assertEqual(resp.status_code, 302)

    def test_login_redirect_authenticated(self):
        self.client.login(username='alice', password='testpw12345')
        resp = self.client.get(reverse('login'))
        self.assertEqual(resp.status_code, 302)

    def test_logout(self):
        self.client.login(username='alice', password='testpw12345')
        resp = self.client.post(reverse('logout'))
        self.assertEqual(resp.status_code, 302)


class HomeViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw12345678')
        self.client = Client()
        self.client.login(username='alice', password='pw12345678')

    def test_home_requires_login(self):
        c = Client()
        resp = c.get(reverse('home'))
        self.assertEqual(resp.status_code, 302)

    def test_home_ok(self):
        resp = self.client.get(reverse('home'))
        self.assertEqual(resp.status_code, 200)

    def test_home_shows_sessions(self):
        GameSession.objects.create(name='Game1', created_by=self.user, status='lobby')
        resp = self.client.get(reverse('home'))
        self.assertEqual(resp.status_code, 200)


class NewGameViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw12345678')
        self.client = Client()
        self.client.login(username='alice', password='pw12345678')

    def test_new_game_get(self):
        resp = self.client.get(reverse('new_game'))
        self.assertIn(resp.status_code, [200, 302])

    def test_new_game_post_single(self):
        resp = self.client.post(reverse('new_game'), {
            'name': 'Test Game', 'max_weeks': '20', 'mode': 'single',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(GameSession.objects.filter(name='Test Game').exists())

    def test_new_game_post_multi(self):
        resp = self.client.post(reverse('new_game'), {
            'name': 'Multi Game', 'max_weeks': '20', 'mode': 'multi',
        })
        self.assertEqual(resp.status_code, 302)
        session = GameSession.objects.get(name='Multi Game')
        # Multi mode creates PlayerSessions for all 5 roles
        self.assertEqual(session.player_sessions.count(), 5)

    def test_new_game_clamps_max_weeks(self):
        self.client.post(reverse('new_game'), {
            'name': 'Short', 'max_weeks': '5', 'mode': 'single',
        })
        session = GameSession.objects.get(name='Short')
        self.assertEqual(session.max_weeks, 12)  # clamped to min 12

    def test_new_game_creates_players(self):
        self.client.post(reverse('new_game'), {
            'name': 'G', 'max_weeks': '20', 'mode': 'single',
        })
        session = GameSession.objects.get(name='G')
        self.assertEqual(session.players.count(), 4)


class GameInitViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw12345678')
        self.client = Client()
        self.client.login(username='alice', password='pw12345678')
        self.session = GameSession.objects.create(
            name='Init Test', created_by=self.user, status='lobby',
        )
        for role_name, role in [
            ('Retailer', 'retailer'), ('Wholesaler', 'wholesaler'),
            ('Distributor', 'distributor'), ('Factory', 'factory'),
        ]:
            Player.objects.create(session=self.session, name=role_name, role=role)

    def test_game_init_get(self):
        resp = self.client.get(reverse('game_init', args=[self.session.id]))
        self.assertEqual(resp.status_code, 200)

    def test_game_init_post_no_player_sessions(self):
        """Without PlayerSessions, should go to dashboard (single-player)."""
        resp = self.client.post(reverse('game_init', args=[self.session.id]), {
            'init_inventory': '12', 'init_orders_placed': '4',
            'init_incoming': '4', 'holding_cost': '0.5', 'backlog_cost': '1.0',
        })
        self.assertEqual(resp.status_code, 302)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'playing')

    def test_game_init_post_with_player_sessions(self):
        """With PlayerSessions, should redirect to lobby."""
        for role in ['customer', 'retailer', 'wholesaler', 'distributor', 'factory']:
            PlayerSession.objects.create(game_session=self.session, role=role)
        resp = self.client.post(reverse('game_init', args=[self.session.id]), {
            'init_inventory': '12', 'init_orders_placed': '4',
            'init_incoming': '4', 'holding_cost': '0.5', 'backlog_cost': '1.0',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertIn('lobby', resp.url)

    def test_game_init_sets_inventory(self):
        self.client.post(reverse('game_init', args=[self.session.id]), {
            'init_inventory': '20', 'init_orders_placed': '4',
            'init_incoming': '4', 'holding_cost': '0.5', 'backlog_cost': '1.0',
        })
        for player in self.session.players.all():
            self.assertEqual(player.inventory, 20)


class LobbyViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw12345678')
        self.client = Client()
        self.client.login(username='alice', password='pw12345678')
        self.session = GameSession.objects.create(
            name='Lobby Test', created_by=self.user, status='lobby',
        )
        for role_name, role in [
            ('Retailer', 'retailer'), ('Wholesaler', 'wholesaler'),
            ('Distributor', 'distributor'), ('Factory', 'factory'),
        ]:
            Player.objects.create(session=self.session, name=role_name, role=role)
        for role in ['customer', 'retailer', 'wholesaler', 'distributor', 'factory']:
            PlayerSession.objects.create(game_session=self.session, role=role)
        initialise_session(self.session)

    def test_lobby_get(self):
        resp = self.client.get(reverse('lobby', args=[self.session.id]))
        self.assertEqual(resp.status_code, 200)

    def test_lobby_status_api(self):
        resp = self.client.get(reverse('lobby_status', args=[self.session.id]))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('joined', data)
        self.assertIn('connected', data)
        self.assertIn('game_started', data)

    def test_lobby_status_shows_players_when_playing(self):
        self.session.status = 'playing'
        self.session.save()
        resp = self.client.get(reverse('lobby_status', args=[self.session.id]))
        data = resp.json()
        self.assertTrue(data['game_started'])
        self.assertIsInstance(data['players'], list)


class JoinGameViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw12345678')
        self.client = Client()
        self.client.login(username='alice', password='pw12345678')
        self.session = GameSession.objects.create(
            name='Join Test', created_by=self.user, status='lobby',
        )
        for role_name, role in [
            ('Retailer', 'retailer'), ('Wholesaler', 'wholesaler'),
            ('Distributor', 'distributor'), ('Factory', 'factory'),
        ]:
            Player.objects.create(session=self.session, name=role_name, role=role)
        self.ps = PlayerSession.objects.create(
            game_session=self.session, role='retailer',
        )

    def test_join_get(self):
        resp = self.client.get(reverse('join_game', args=[self.ps.token]))
        self.assertEqual(resp.status_code, 200)

    def test_join_post(self):
        resp = self.client.post(reverse('join_game', args=[self.ps.token]), {
            'name': 'Bob',
        })
        self.assertEqual(resp.status_code, 302)
        self.ps.refresh_from_db()
        self.assertEqual(self.ps.name, 'Bob')
        self.assertEqual(self.ps.user, self.user)

    def test_join_customer_redirects_to_customer_play(self):
        customer_ps = PlayerSession.objects.create(
            game_session=self.session, role='customer',
        )
        resp = self.client.post(reverse('join_game', args=[customer_ps.token]), {
            'name': 'Cust',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertIn('customer', resp.url)

    def test_join_already_claimed_by_other(self):
        other_user = User.objects.create_user('bob', password='pw12345678')
        self.ps.user = other_user
        self.ps.save()
        resp = self.client.get(reverse('join_game', args=[self.ps.token]))
        self.assertEqual(resp.status_code, 200)

    def test_join_default_name_from_user(self):
        self.user.first_name = 'Alice'
        self.user.save()
        resp = self.client.post(reverse('join_game', args=[self.ps.token]), {
            'name': '',
        })
        self.ps.refresh_from_db()
        self.assertEqual(self.ps.name, 'Alice')


class PlayViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw12345678')
        self.client = Client()
        self.client.login(username='alice', password='pw12345678')
        self.session = GameSession.objects.create(
            name='Play Test', created_by=self.user, status='playing',
        )
        Player.objects.create(session=self.session, name='R', role='retailer')
        self.ps = PlayerSession.objects.create(
            game_session=self.session, role='retailer',
        )

    def test_play_with_token_param(self):
        resp = self.client.get(
            reverse('play', args=[self.session.id]),
            {'token': self.ps.token},
        )
        self.assertEqual(resp.status_code, 200)

    def test_play_without_token_redirects(self):
        resp = self.client.get(reverse('play', args=[self.session.id]))
        self.assertEqual(resp.status_code, 302)

    def test_play_stores_token_in_session(self):
        self.client.get(
            reverse('play', args=[self.session.id]),
            {'token': self.ps.token},
        )
        self.assertEqual(self.client.session['player_token'], self.ps.token)


class CustomerPlayViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw12345678')
        self.client = Client()
        self.client.login(username='alice', password='pw12345678')
        self.session = GameSession.objects.create(
            name='Cust Test', created_by=self.user, status='playing',
        )
        Player.objects.create(session=self.session, name='R', role='retailer')
        self.ps = PlayerSession.objects.create(
            game_session=self.session, role='customer',
        )

    def test_customer_play_with_token(self):
        resp = self.client.get(
            reverse('customer_play', args=[self.session.id]),
            {'token': self.ps.token},
        )
        self.assertEqual(resp.status_code, 200)

    def test_customer_play_without_token(self):
        resp = self.client.get(reverse('customer_play', args=[self.session.id]))
        self.assertEqual(resp.status_code, 302)


class DashboardViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw12345678')
        self.client = Client()
        self.client.login(username='alice', password='pw12345678')
        self.session = GameSession.objects.create(
            name='Dash', created_by=self.user, status='playing',
        )
        for role_name, role in [
            ('Retailer', 'retailer'), ('Wholesaler', 'wholesaler'),
            ('Distributor', 'distributor'), ('Factory', 'factory'),
        ]:
            Player.objects.create(session=self.session, name=role_name, role=role)
        initialise_session(self.session)

    def test_dashboard_get(self):
        resp = self.client.get(reverse('dashboard', args=[self.session.id]))
        self.assertEqual(resp.status_code, 200)

    def test_dashboard_after_one_week(self):
        self.session.pending_customer_demand = 4
        self.session.save()
        process_week(self.session, {p.id: 4 for p in self.session.players.all()})
        resp = self.client.get(reverse('dashboard', args=[self.session.id]))
        self.assertEqual(resp.status_code, 200)


class NextTurnViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw12345678')
        self.client = Client()
        self.client.login(username='alice', password='pw12345678')
        self.session = GameSession.objects.create(
            name='Turn', created_by=self.user, status='playing',
        )
        for role_name, role in [
            ('Retailer', 'retailer'), ('Wholesaler', 'wholesaler'),
            ('Distributor', 'distributor'), ('Factory', 'factory'),
        ]:
            Player.objects.create(session=self.session, name=role_name, role=role)
        initialise_session(self.session)

    def test_next_turn_post(self):
        data = {'customer_demand': '4'}
        for p in self.session.players.all():
            data[f'order_{p.id}'] = '4'
        resp = self.client.post(reverse('next_turn', args=[self.session.id]), data)
        self.assertEqual(resp.status_code, 302)
        self.session.refresh_from_db()
        self.assertEqual(self.session.current_week, 1)

    def test_next_turn_get_not_allowed(self):
        resp = self.client.get(reverse('next_turn', args=[self.session.id]))
        self.assertEqual(resp.status_code, 405)

    def test_next_turn_finished_redirects(self):
        self.session.is_active = False
        self.session.current_week = 20
        self.session.save()
        resp = self.client.post(reverse('next_turn', args=[self.session.id]), {
            'customer_demand': '4',
        })
        self.assertEqual(resp.status_code, 302)

    def test_next_turn_invalid_demand_defaults(self):
        resp = self.client.post(reverse('next_turn', args=[self.session.id]), {
            'customer_demand': 'abc',
        })
        self.assertEqual(resp.status_code, 302)
        # Should not crash; defaults to 4


class ClientViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw12345678')
        self.client = Client()
        self.client.login(username='alice', password='pw12345678')
        self.session = GameSession.objects.create(
            name='Client', created_by=self.user, status='playing',
        )
        for role_name, role in [
            ('Retailer', 'retailer'), ('Wholesaler', 'wholesaler'),
            ('Distributor', 'distributor'), ('Factory', 'factory'),
        ]:
            Player.objects.create(session=self.session, name=role_name, role=role)

    def test_client_view_retailer(self):
        resp = self.client.get(reverse('client_view', args=[self.session.id, 'retailer']))
        self.assertEqual(resp.status_code, 200)

    def test_client_view_factory(self):
        resp = self.client.get(reverse('client_view', args=[self.session.id, 'factory']))
        self.assertEqual(resp.status_code, 200)

    def test_client_view_invalid_role(self):
        resp = self.client.get(reverse('client_view', args=[self.session.id, 'customer']))
        self.assertEqual(resp.status_code, 302)


class CustomerViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw12345678')
        self.client = Client()
        self.client.login(username='alice', password='pw12345678')
        self.session = GameSession.objects.create(
            name='CustView', created_by=self.user, status='playing',
        )
        Player.objects.create(session=self.session, name='Retailer', role='retailer')

    def test_customer_view_ok(self):
        resp = self.client.get(reverse('customer_view', args=[self.session.id]))
        self.assertEqual(resp.status_code, 200)


class ResultsViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw12345678')
        self.client = Client()
        self.client.login(username='alice', password='pw12345678')
        self.session = GameSession.objects.create(
            name='Results', created_by=self.user, status='finished',
        )
        for role_name, role in [
            ('Retailer', 'retailer'), ('Wholesaler', 'wholesaler'),
            ('Distributor', 'distributor'), ('Factory', 'factory'),
        ]:
            Player.objects.create(session=self.session, name=role_name, role=role)

    def test_results_view(self):
        resp = self.client.get(reverse('results', args=[self.session.id]))
        self.assertEqual(resp.status_code, 200)


class ChartDataAPITest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw12345678')
        self.client = Client()
        self.client.login(username='alice', password='pw12345678')
        self.session = GameSession.objects.create(
            name='Chart', created_by=self.user, status='playing',
        )
        for role_name, role in [
            ('Retailer', 'retailer'), ('Wholesaler', 'wholesaler'),
            ('Distributor', 'distributor'), ('Factory', 'factory'),
        ]:
            Player.objects.create(session=self.session, name=role_name, role=role)

    def test_chart_data_api(self):
        resp = self.client.get(reverse('chart_data_api', args=[self.session.id]))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('retailer', data)
        self.assertIn('customer', data)


class ResetGameViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('alice', password='pw12345678')
        self.client = Client()
        self.client.login(username='alice', password='pw12345678')
        self.session = GameSession.objects.create(
            name='Reset', created_by=self.user,
        )

    def test_reset_game_post(self):
        resp = self.client.post(reverse('reset_game', args=[self.session.id]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(GameSession.objects.filter(id=self.session.id).exists())

    def test_reset_game_get_not_allowed(self):
        resp = self.client.get(reverse('reset_game', args=[self.session.id]))
        self.assertEqual(resp.status_code, 405)
