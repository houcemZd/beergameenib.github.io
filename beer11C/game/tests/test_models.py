"""
Tests for game.models — GameSession, Player, PlayerSession, WeeklyState,
PipelineOrder, PipelineShipment, CustomerDemand.
"""
from django.test import TestCase
from django.contrib.auth.models import User
from game.models import (
    GameSession, Player, PlayerSession, WeeklyState,
    PipelineOrder, PipelineShipment, CustomerDemand,
)


class GameSessionModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('creator', password='pw12345678')
        self.session = GameSession.objects.create(
            name='Test Game', max_weeks=20, created_by=self.user,
        )

    def test_str(self):
        self.assertEqual(str(self.session), 'Test Game — Week 0')

    def test_is_finished_false(self):
        self.assertFalse(self.session.is_finished)

    def test_is_finished_true(self):
        self.session.current_week = 20
        self.assertTrue(self.session.is_finished)

    def test_channel_group_name(self):
        self.assertEqual(self.session.channel_group_name, f'game_{self.session.id}')

    # ── Submitted roles ──────────────────────────────────────────────────────
    def test_submitted_role_list_empty(self):
        self.assertEqual(self.session.submitted_role_list, [])

    def test_mark_submitted(self):
        self.session.mark_submitted('retailer')
        self.assertIn('retailer', self.session.submitted_role_list)

    def test_mark_submitted_idempotent(self):
        self.session.mark_submitted('retailer')
        self.session.mark_submitted('retailer')
        self.assertEqual(self.session.submitted_role_list.count('retailer'), 1)

    def test_all_submitted_no_players(self):
        # No player_sessions → trivially true
        self.assertTrue(self.session.all_submitted())

    def test_all_submitted_with_players(self):
        PlayerSession.objects.create(game_session=self.session, role='retailer')
        PlayerSession.objects.create(game_session=self.session, role='factory')
        self.assertFalse(self.session.all_submitted())
        self.session.mark_submitted('retailer')
        self.assertFalse(self.session.all_submitted())
        self.session.mark_submitted('factory')
        self.assertTrue(self.session.all_submitted())

    # ── Ready roles ──────────────────────────────────────────────────────────
    def test_ready_role_list_empty(self):
        self.assertEqual(self.session.ready_role_list, [])

    def test_mark_ready(self):
        self.session.mark_ready('wholesaler')
        self.assertIn('wholesaler', self.session.ready_role_list)

    def test_mark_ready_idempotent(self):
        self.session.mark_ready('wholesaler')
        self.session.mark_ready('wholesaler')
        self.assertEqual(self.session.ready_role_list.count('wholesaler'), 1)

    def test_all_ready_no_players(self):
        self.assertTrue(self.session.all_ready())

    def test_all_ready_with_players(self):
        PlayerSession.objects.create(game_session=self.session, role='retailer')
        self.assertFalse(self.session.all_ready())
        self.session.mark_ready('retailer')
        self.assertTrue(self.session.all_ready())

    # ── Reset ────────────────────────────────────────────────────────────────
    def test_reset_submissions(self):
        self.session.mark_submitted('retailer')
        self.session.pending_customer_demand = 5
        self.session.save()
        self.session.reset_submissions()
        self.session.refresh_from_db()
        self.assertEqual(self.session.submitted_roles, '')
        self.assertIsNone(self.session.pending_customer_demand)


class PlayerModelTest(TestCase):
    def setUp(self):
        self.session = GameSession.objects.create(name='Test')
        self.retailer = Player.objects.create(session=self.session, name='R', role='retailer')
        self.wholesaler = Player.objects.create(session=self.session, name='W', role='wholesaler')
        self.distributor = Player.objects.create(session=self.session, name='D', role='distributor')
        self.factory = Player.objects.create(session=self.session, name='F', role='factory')

    def test_str(self):
        self.assertEqual(str(self.retailer), 'R (retailer)')

    def test_get_downstream_retailer(self):
        """Retailer has no downstream (customer is external)."""
        self.assertIsNone(self.retailer.get_downstream())

    def test_get_downstream_wholesaler(self):
        self.assertEqual(self.wholesaler.get_downstream(), self.retailer)

    def test_get_downstream_distributor(self):
        self.assertEqual(self.distributor.get_downstream(), self.wholesaler)

    def test_get_downstream_factory(self):
        self.assertEqual(self.factory.get_downstream(), self.distributor)

    def test_get_upstream_retailer(self):
        self.assertEqual(self.retailer.get_upstream(), self.wholesaler)

    def test_get_upstream_wholesaler(self):
        self.assertEqual(self.wholesaler.get_upstream(), self.distributor)

    def test_get_upstream_distributor(self):
        self.assertEqual(self.distributor.get_upstream(), self.factory)

    def test_get_upstream_factory(self):
        """Factory has no upstream."""
        self.assertIsNone(self.factory.get_upstream())

    def test_default_inventory(self):
        self.assertEqual(self.retailer.inventory, 12)

    def test_default_costs(self):
        self.assertEqual(self.retailer.holding_cost, 0.5)
        self.assertEqual(self.retailer.backlog_cost, 1.0)


class PlayerSessionModelTest(TestCase):
    def setUp(self):
        self.session = GameSession.objects.create(name='Test')
        self.ps = PlayerSession.objects.create(game_session=self.session, role='retailer')

    def test_str(self):
        s = str(self.ps)
        self.assertIn('retailer', s)
        self.assertIn('Test', s)

    def test_token_unique(self):
        """Each PlayerSession auto-generates a unique token."""
        ps2 = PlayerSession.objects.create(game_session=self.session, role='wholesaler')
        self.assertNotEqual(self.ps.token, ps2.token)

    def test_default_phase(self):
        self.assertEqual(self.ps.turn_phase, PlayerSession.PHASE_IDLE)

    def test_unique_together(self):
        """Can't have two PlayerSessions for same role in same game."""
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            PlayerSession.objects.create(game_session=self.session, role='retailer')


class WeeklyStateModelTest(TestCase):
    def setUp(self):
        session = GameSession.objects.create(name='T')
        self.player = Player.objects.create(session=session, name='R', role='retailer')

    def test_str(self):
        ws = WeeklyState.objects.create(
            player=self.player, week=1, inventory=10, backlog=2,
        )
        self.assertEqual(str(ws), 'R (retailer) W1')

    def test_unique_together(self):
        from django.db import IntegrityError
        WeeklyState.objects.create(player=self.player, week=1, inventory=10, backlog=0)
        with self.assertRaises(IntegrityError):
            WeeklyState.objects.create(player=self.player, week=1, inventory=5, backlog=0)


class PipelineOrderModelTest(TestCase):
    def setUp(self):
        session = GameSession.objects.create(name='T')
        self.player = Player.objects.create(session=session, name='R', role='retailer')

    def test_str(self):
        o = PipelineOrder.objects.create(
            sender=self.player, quantity=4, placed_on_week=1, arrives_on_week=3,
        )
        self.assertIn('4', str(o))
        self.assertIn('W3', str(o))


class PipelineShipmentModelTest(TestCase):
    def setUp(self):
        session = GameSession.objects.create(name='T')
        self.player = Player.objects.create(session=session, name='R', role='retailer')

    def test_str(self):
        s = PipelineShipment.objects.create(
            receiver=self.player, quantity=6, shipped_on_week=1, arrives_on_week=3,
        )
        self.assertIn('6', str(s))
        self.assertIn('W3', str(s))


class CustomerDemandModelTest(TestCase):
    def setUp(self):
        self.session = GameSession.objects.create(name='T')

    def test_str(self):
        d = CustomerDemand.objects.create(session=self.session, week=1, quantity=8)
        self.assertEqual(str(d), 'Demand W1: 8')

    def test_ordering(self):
        CustomerDemand.objects.create(session=self.session, week=3, quantity=5)
        CustomerDemand.objects.create(session=self.session, week=1, quantity=8)
        demands = list(CustomerDemand.objects.filter(session=self.session))
        self.assertEqual(demands[0].week, 1)
        self.assertEqual(demands[1].week, 3)
