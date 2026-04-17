"""
Tests for HTTP views (authorization, redirects, response codes).
"""
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from game.models import GameSession, Player, PlayerSession
from game.services import initialise_session
import secrets


def _make_user(username='alice', password='securepass99'):
    return User.objects.create_user(username, password=password)


def _make_session(user=None, status=GameSession.STATUS_PLAYING):
    s = GameSession.objects.create(
        name='Test', max_weeks=20, status=status, created_by=user,
    )
    for role in ['retailer', 'wholesaler', 'distributor', 'factory']:
        Player.objects.create(session=s, name=role.title(), role=role)
    return s


def _make_player_session(session, role='retailer', user=None):
    return PlayerSession.objects.create(
        game_session=session, role=role, user=user,
        token=secrets.token_urlsafe(32),
    )


class AuthRedirectTest(TestCase):
    """All game views must redirect anonymous users to login."""

    def test_home_requires_login(self):
        r = self.client.get(reverse('home'))
        self.assertRedirects(r, '/accounts/login/?next=/', fetch_redirect_response=False)

    def test_new_game_requires_login(self):
        r = self.client.get(reverse('new_game'))
        self.assertIn('/accounts/login/', r['Location'])

    def test_dashboard_requires_login(self):
        session = _make_session()
        r = self.client.get(reverse('dashboard', args=[session.id]))
        self.assertIn('/accounts/login/', r['Location'])

    def test_results_requires_login(self):
        session = _make_session()
        r = self.client.get(reverse('results', args=[session.id]))
        self.assertIn('/accounts/login/', r['Location'])

    def test_reset_game_requires_login(self):
        session = _make_session()
        r = self.client.post(reverse('reset_game', args=[session.id]))
        self.assertIn('/accounts/login/', r['Location'])


class HomeViewTest(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.client.login(username='alice', password='securepass99')

    def test_200(self):
        r = self.client.get(reverse('home'))
        self.assertEqual(r.status_code, 200)

    def test_my_sessions_visible(self):
        session = _make_session(user=self.user)
        r = self.client.get(reverse('home'))
        self.assertIn(session, r.context['my_sessions'])

    def test_other_user_sessions_not_in_my_sessions(self):
        other = _make_user('bob', 'securepass99')
        session = _make_session(user=other)
        r = self.client.get(reverse('home'))
        self.assertNotIn(session, r.context['my_sessions'])


class NewGameViewTest(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.client.login(username='alice', password='securepass99')

    def test_get_200(self):
        r = self.client.get(reverse('new_game'))
        self.assertEqual(r.status_code, 200)

    def test_post_creates_session(self):
        r = self.client.post(reverse('new_game'), {
            'name': 'My Game', 'max_weeks': '20', 'mode': 'single',
        })
        self.assertEqual(GameSession.objects.filter(name='My Game').count(), 1)

    def test_post_redirects_to_game_init(self):
        r = self.client.post(reverse('new_game'), {
            'name': 'My Game', 'max_weeks': '20', 'mode': 'single',
        })
        session = GameSession.objects.get(name='My Game')
        self.assertRedirects(r, reverse('game_init', args=[session.id]))


class GameInitAuthorizationTest(TestCase):
    def setUp(self):
        self.owner = _make_user('owner', 'securepass99')
        self.other = _make_user('other', 'securepass99')
        self.staff = _make_user('staff', 'securepass99')
        self.staff.is_staff = True
        self.staff.save(update_fields=['is_staff'])
        self.session = _make_session(user=self.owner)

    def test_owner_can_access(self):
        self.client.login(username='owner', password='securepass99')
        r = self.client.get(reverse('game_init', args=[self.session.id]))
        self.assertEqual(r.status_code, 200)

    def test_other_user_gets_403(self):
        self.client.login(username='other', password='securepass99')
        r = self.client.get(reverse('game_init', args=[self.session.id]))
        self.assertEqual(r.status_code, 403)

    def test_staff_user_can_access(self):
        self.client.login(username='staff', password='securepass99')
        r = self.client.get(reverse('game_init', args=[self.session.id]))
        self.assertEqual(r.status_code, 200)


class DashboardAuthorizationTest(TestCase):
    def setUp(self):
        self.owner = _make_user('owner', 'securepass99')
        self.other = _make_user('other', 'securepass99')
        self.session = _make_session(user=self.owner)
        initialise_session(self.session)

    def test_owner_can_access(self):
        self.client.login(username='owner', password='securepass99')
        r = self.client.get(reverse('dashboard', args=[self.session.id]))
        self.assertEqual(r.status_code, 200)

    def test_non_member_gets_403(self):
        self.client.login(username='other', password='securepass99')
        r = self.client.get(reverse('dashboard', args=[self.session.id]))
        self.assertEqual(r.status_code, 403)

    def test_player_member_can_access(self):
        self.client.login(username='other', password='securepass99')
        _make_player_session(self.session, 'retailer', user=self.other)
        r = self.client.get(reverse('dashboard', args=[self.session.id]))
        self.assertEqual(r.status_code, 200)


class ResetGameAuthorizationTest(TestCase):
    def setUp(self):
        self.owner = _make_user('owner', 'securepass99')
        self.other = _make_user('other', 'securepass99')
        self.staff = _make_user('staff', 'securepass99')
        self.staff.is_staff = True
        self.staff.save(update_fields=['is_staff'])
        self.session = _make_session(user=self.owner)

    def test_owner_can_reset(self):
        self.client.login(username='owner', password='securepass99')
        r = self.client.post(reverse('reset_game', args=[self.session.id]))
        self.assertRedirects(r, reverse('home'))
        self.assertFalse(GameSession.objects.filter(id=self.session.id).exists())

    def test_other_user_cannot_reset(self):
        self.client.login(username='other', password='securepass99')
        r = self.client.post(reverse('reset_game', args=[self.session.id]))
        self.assertEqual(r.status_code, 403)
        self.assertTrue(GameSession.objects.filter(id=self.session.id).exists())

    def test_staff_user_can_reset(self):
        self.client.login(username='staff', password='securepass99')
        r = self.client.post(reverse('reset_game', args=[self.session.id]))
        self.assertRedirects(r, reverse('home'))
        self.assertFalse(GameSession.objects.filter(id=self.session.id).exists())


class NextTurnAuthorizationTest(TestCase):
    def setUp(self):
        self.owner = _make_user('owner', 'securepass99')
        self.other = _make_user('other', 'securepass99')
        self.staff = _make_user('staff', 'securepass99')
        self.staff.is_staff = True
        self.staff.save(update_fields=['is_staff'])
        self.session = _make_session(user=self.owner)
        initialise_session(self.session)

    def test_other_user_gets_403(self):
        self.client.login(username='other', password='securepass99')
        r = self.client.post(reverse('next_turn', args=[self.session.id]), {
            'customer_demand': '4',
        })
        self.assertEqual(r.status_code, 403)

    def test_staff_user_can_advance_turn(self):
        self.client.login(username='staff', password='securepass99')
        r = self.client.post(reverse('next_turn', args=[self.session.id]), {
            'customer_demand': '4',
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, reverse('dashboard', args=[self.session.id]))


class ResultsAuthorizationTest(TestCase):
    def setUp(self):
        self.owner = _make_user('owner', 'securepass99')
        self.other = _make_user('other', 'securepass99')
        self.session = _make_session(user=self.owner, status=GameSession.STATUS_FINISHED)

    def test_owner_can_view(self):
        self.client.login(username='owner', password='securepass99')
        r = self.client.get(reverse('results', args=[self.session.id]))
        self.assertEqual(r.status_code, 200)

    def test_non_member_gets_403(self):
        self.client.login(username='other', password='securepass99')
        r = self.client.get(reverse('results', args=[self.session.id]))
        self.assertEqual(r.status_code, 403)

    def test_player_can_view(self):
        self.client.login(username='other', password='securepass99')
        _make_player_session(self.session, 'retailer', user=self.other)
        r = self.client.get(reverse('results', args=[self.session.id]))
        self.assertEqual(r.status_code, 200)


class LobbyStatusAuthorizationTest(TestCase):
    def setUp(self):
        self.owner = _make_user('owner', 'securepass99')
        self.other = _make_user('other', 'securepass99')
        self.session = _make_session(user=self.owner, status=GameSession.STATUS_LOBBY)

    def test_owner_gets_200(self):
        self.client.login(username='owner', password='securepass99')
        r = self.client.get(reverse('lobby_status', args=[self.session.id]))
        self.assertEqual(r.status_code, 200)

    def test_non_member_gets_403(self):
        self.client.login(username='other', password='securepass99')
        r = self.client.get(reverse('lobby_status', args=[self.session.id]))
        self.assertEqual(r.status_code, 403)


class LobbyStartGameAuthorizationTest(TestCase):
    def setUp(self):
        self.owner = _make_user('owner', 'securepass99')
        self.other = _make_user('other', 'securepass99')
        self.staff = _make_user('staff', 'securepass99')
        self.staff.is_staff = True
        self.staff.save(update_fields=['is_staff'])
        self.session = _make_session(user=self.owner, status=GameSession.STATUS_LOBBY)
        _make_player_session(self.session, 'retailer', user=self.owner)
        _make_player_session(self.session, 'wholesaler', user=self.other)
        PlayerSession.objects.filter(game_session=self.session, role='retailer').update(name='Owner')
        PlayerSession.objects.filter(game_session=self.session, role='wholesaler').update(name='Other')

    def test_non_owner_non_staff_gets_403(self):
        self.client.login(username='other', password='securepass99')
        r = self.client.post(reverse('lobby_start_game', args=[self.session.id]))
        self.assertEqual(r.status_code, 403)

    def test_staff_user_can_start_game(self):
        self.client.login(username='staff', password='securepass99')
        r = self.client.post(reverse('lobby_start_game', args=[self.session.id]))
        self.assertEqual(r.status_code, 200)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, GameSession.STATUS_PLAYING)

    def test_anonymous_user_with_ownerless_session_gets_403(self):
        """Any non-staff user must not be able to start a session whose creator was deleted."""
        ownerless = _make_session(user=None, status=GameSession.STATUS_LOBBY)
        _make_player_session(ownerless, 'retailer', user=self.owner)
        PlayerSession.objects.filter(game_session=ownerless, role='retailer').update(name='Owner')
        _make_player_session(ownerless, 'wholesaler', user=self.other)
        PlayerSession.objects.filter(game_session=ownerless, role='wholesaler').update(name='Other')
        # Even a player-member of the session cannot start it
        self.client.login(username='other', password='securepass99')
        r = self.client.post(reverse('lobby_start_game', args=[ownerless.id]))
        self.assertEqual(r.status_code, 403)


class ChartDataAPIAuthorizationTest(TestCase):
    def setUp(self):
        self.owner = _make_user('owner', 'securepass99')
        self.other = _make_user('other', 'securepass99')
        self.session = _make_session(user=self.owner)

    def test_owner_gets_200(self):
        self.client.login(username='owner', password='securepass99')
        r = self.client.get(reverse('chart_data_api', args=[self.session.id]))
        self.assertEqual(r.status_code, 200)

    def test_non_member_gets_403(self):
        self.client.login(username='other', password='securepass99')
        r = self.client.get(reverse('chart_data_api', args=[self.session.id]))
        self.assertEqual(r.status_code, 403)


class JoinGameViewTest(TestCase):
    def setUp(self):
        self.owner = _make_user('owner', 'securepass99')
        self.player_user = _make_user('player', 'securepass99')
        self.session = _make_session(user=self.owner, status=GameSession.STATUS_LOBBY)
        self.ps = _make_player_session(self.session, 'retailer')

    def test_get_join_page(self):
        self.client.login(username='player', password='securepass99')
        r = self.client.get(reverse('join_game', args=[self.ps.token]))
        self.assertEqual(r.status_code, 200)

    def test_post_sets_name(self):
        self.client.login(username='player', password='securepass99')
        self.client.post(reverse('join_game', args=[self.ps.token]), {'name': 'Bob'})
        self.ps.refresh_from_db()
        self.assertEqual(self.ps.name, 'Bob')
