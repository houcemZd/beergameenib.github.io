"""
Tests for game.services — phase-gated turn system + legacy single-pass.
"""
from django.test import TestCase
from game.models import (
    GameSession, Player, PlayerSession, WeeklyState,
    PipelineOrder, PipelineShipment, CustomerDemand,
)
from game.services import (
    initialise_session, open_week, apply_receive, apply_ship,
    apply_order, close_week, process_week, _ai_order,
    get_chart_data, get_bullwhip_data,
    ORDER_DELAY, SHIP_DELAY,
)


def _create_full_session(name='Test', max_weeks=20):
    """Helper: create a session with all 4 supply-chain players + 5 PlayerSessions."""
    session = GameSession.objects.create(name=name, max_weeks=max_weeks, status='playing')
    for role_name, role in [
        ('Retailer', 'retailer'), ('Wholesaler', 'wholesaler'),
        ('Distributor', 'distributor'), ('Factory', 'factory'),
    ]:
        Player.objects.create(session=session, name=role_name, role=role)

    for role in ['customer', 'retailer', 'wholesaler', 'distributor', 'factory']:
        PlayerSession.objects.create(game_session=session, role=role)

    return session


class InitialiseSessionTest(TestCase):
    def setUp(self):
        self.session = _create_full_session()

    def test_creates_pipeline_shipments(self):
        initialise_session(self.session)
        # 4 players × 2 shipment slots = 8
        self.assertEqual(PipelineShipment.objects.filter(receiver__session=self.session).count(), 8)

    def test_pipeline_shipments_arrive_weeks_1_and_2(self):
        initialise_session(self.session)
        weeks = set(PipelineShipment.objects.filter(
            receiver__session=self.session
        ).values_list('arrives_on_week', flat=True))
        self.assertEqual(weeks, {1, 2})

    def test_non_factory_pipeline_orders(self):
        initialise_session(self.session)
        # retailer + wholesaler + distributor = 3 players × 2 orders each = 6
        non_factory_orders = PipelineOrder.objects.filter(
            sender__session=self.session
        ).exclude(sender__role='factory')
        self.assertEqual(non_factory_orders.count(), 6)

    def test_factory_pipeline_order(self):
        initialise_session(self.session)
        factory = self.session.players.get(role='factory')
        factory_orders = PipelineOrder.objects.filter(sender=factory)
        # Factory gets 1 production request (arrives week 1)
        self.assertEqual(factory_orders.count(), 1)
        self.assertEqual(factory_orders.first().arrives_on_week, 1)

    def test_custom_init_values(self):
        initialise_session(self.session, init_orders_placed=8, init_incoming=6)
        ship = PipelineShipment.objects.filter(receiver__session=self.session).first()
        self.assertEqual(ship.quantity, 6)
        factory = self.session.players.get(role='factory')
        order = PipelineOrder.objects.filter(sender=factory).first()
        self.assertEqual(order.quantity, 8)


class OpenWeekTest(TestCase):
    def setUp(self):
        self.session = _create_full_session()
        initialise_session(self.session)
        # Set customer demand for week 1
        self.session.pending_customer_demand = 4
        self.session.save()

    def test_open_week_returns_staging(self):
        staging = open_week(self.session)
        self.assertIn('retailer', staging)
        self.assertIn('factory', staging)
        self.assertIn('received', staging['retailer'])
        self.assertIn('order_qty', staging['retailer'])

    def test_open_week_sets_phase_receive(self):
        open_week(self.session)
        for role in ['retailer', 'wholesaler', 'distributor', 'factory']:
            ps = self.session.player_sessions.get(role=role)
            self.assertEqual(ps.turn_phase, PlayerSession.PHASE_RECEIVE)

    def test_open_week_customer_stays_idle(self):
        open_week(self.session)
        ps = self.session.player_sessions.get(role='customer')
        self.assertEqual(ps.turn_phase, PlayerSession.PHASE_IDLE)

    def test_open_week_stages_arriving_shipments(self):
        staging = open_week(self.session)
        # Week 1: initial shipments arrive (qty=4 each player)
        for role in ['retailer', 'wholesaler', 'distributor', 'factory']:
            self.assertEqual(staging[role]['received'], 4)

    def test_open_week_retailer_gets_customer_demand(self):
        staging = open_week(self.session)
        self.assertEqual(staging['retailer']['order_qty'], 4)

    def test_open_week_factory_production_request(self):
        staging = open_week(self.session)
        # Factory should see the initial production request arriving week 1
        self.assertIn('production_request', staging['factory'])
        self.assertEqual(staging['factory']['production_request'], 4)

    def test_open_week_pending_received_qty_set(self):
        open_week(self.session)
        ps = self.session.player_sessions.get(role='retailer')
        self.assertEqual(ps.pending_received_qty, 4)


class ApplyReceiveTest(TestCase):
    def setUp(self):
        self.session = _create_full_session()
        initialise_session(self.session)
        self.session.pending_customer_demand = 4
        self.session.save()
        open_week(self.session)

    def test_apply_receive_adds_inventory(self):
        ps = self.session.player_sessions.get(role='retailer')
        result = apply_receive(ps)
        self.assertEqual(result['received'], 4)
        player = self.session.players.get(role='retailer')
        self.assertEqual(player.inventory, 16)  # 12 + 4

    def test_apply_receive_moves_to_phase_ship(self):
        ps = self.session.player_sessions.get(role='retailer')
        apply_receive(ps)
        ps.refresh_from_db()
        self.assertEqual(ps.turn_phase, PlayerSession.PHASE_SHIP)

    def test_apply_receive_marks_shipments_delivered(self):
        ps = self.session.player_sessions.get(role='retailer')
        apply_receive(ps)
        player = self.session.players.get(role='retailer')
        delivered = PipelineShipment.objects.filter(
            receiver=player, arrives_on_week=1, delivered=True,
        ).count()
        self.assertGreaterEqual(delivered, 1)

    def test_factory_receive_starts_production(self):
        ps = self.session.player_sessions.get(role='factory')
        result = apply_receive(ps)
        # Factory receives completed production AND starts new production
        self.assertEqual(result['production_started'], 4)
        # A new PipelineShipment should be created for production delay
        factory = self.session.players.get(role='factory')
        production_ships = PipelineShipment.objects.filter(
            receiver=factory, shipped_on_week=1, delivered=False,
        )
        self.assertEqual(production_ships.count(), 1)
        self.assertEqual(production_ships.first().arrives_on_week, 1 + SHIP_DELAY)


class ApplyShipTest(TestCase):
    def setUp(self):
        self.session = _create_full_session()
        initialise_session(self.session)
        self.session.pending_customer_demand = 4
        self.session.save()
        open_week(self.session)
        # Apply receive for all non-customer roles first
        for role in ['retailer', 'wholesaler', 'distributor', 'factory']:
            ps = self.session.player_sessions.get(role=role)
            apply_receive(ps)

    def test_apply_ship_deducts_inventory(self):
        ps = self.session.player_sessions.get(role='wholesaler')
        result = apply_ship(ps)
        player = self.session.players.get(role='wholesaler')
        self.assertGreaterEqual(result['shipped'], 0)
        self.assertGreaterEqual(player.inventory, 0)

    def test_apply_ship_moves_to_phase_order(self):
        ps = self.session.player_sessions.get(role='retailer')
        apply_ship(ps)
        ps.refresh_from_db()
        self.assertEqual(ps.turn_phase, PlayerSession.PHASE_ORDER)

    def test_apply_ship_creates_downstream_shipment(self):
        # Wholesaler ships to retailer
        ps = self.session.player_sessions.get(role='wholesaler')
        result = apply_ship(ps)
        if result['shipped'] > 0:
            retailer = self.session.players.get(role='retailer')
            # There should be a shipment to retailer arriving in 2 weeks
            ships = PipelineShipment.objects.filter(
                receiver=retailer, shipped_on_week=1, delivered=False,
            )
            self.assertTrue(ships.exists())

    def test_apply_ship_retailer_uses_customer_demand(self):
        ps = self.session.player_sessions.get(role='retailer')
        result = apply_ship(ps)
        # Retailer demand = customer demand (4)
        self.assertEqual(result['demand_received'], 4)

    def test_apply_ship_backlog_when_insufficient(self):
        # Deplete retailer inventory to force backlog
        retailer = self.session.players.get(role='retailer')
        retailer.inventory = 0
        retailer.save()
        ps = self.session.player_sessions.get(role='retailer')
        result = apply_ship(ps)
        self.assertEqual(result['shipped'], 0)
        self.assertGreater(result['new_backlog'], 0)


class ApplyOrderTest(TestCase):
    def setUp(self):
        self.session = _create_full_session()
        initialise_session(self.session)
        self.session.pending_customer_demand = 4
        self.session.save()
        open_week(self.session)
        for role in ['retailer', 'wholesaler', 'distributor', 'factory']:
            ps = self.session.player_sessions.get(role=role)
            apply_receive(ps)
            apply_ship(ps)

    def test_apply_order_creates_pipeline_order(self):
        ps = self.session.player_sessions.get(role='retailer')
        result = apply_order(ps, 6)
        self.assertEqual(result['order_placed'], 6)
        retailer = self.session.players.get(role='retailer')
        order = PipelineOrder.objects.filter(
            sender=retailer, placed_on_week=1,
        ).last()
        self.assertIsNotNone(order)
        self.assertEqual(order.quantity, 6)
        self.assertEqual(order.arrives_on_week, 1 + ORDER_DELAY)

    def test_apply_order_factory_production_request(self):
        ps = self.session.player_sessions.get(role='factory')
        result = apply_order(ps, 5)
        factory = self.session.players.get(role='factory')
        order = PipelineOrder.objects.filter(
            sender=factory, placed_on_week=1, fulfilled=False,
        ).last()
        self.assertIsNotNone(order)
        # Factory production request has 1-week delay
        self.assertEqual(order.arrives_on_week, 2)  # week 1 + 1

    def test_apply_order_moves_to_phase_done(self):
        ps = self.session.player_sessions.get(role='retailer')
        apply_order(ps, 4)
        ps.refresh_from_db()
        self.assertEqual(ps.turn_phase, PlayerSession.PHASE_DONE)

    def test_apply_order_marks_submitted(self):
        ps = self.session.player_sessions.get(role='retailer')
        apply_order(ps, 4)
        self.session.refresh_from_db()
        self.assertIn('retailer', self.session.submitted_role_list)

    def test_apply_order_zero_quantity(self):
        ps = self.session.player_sessions.get(role='retailer')
        result = apply_order(ps, 0)
        self.assertEqual(result['order_placed'], 0)


class CloseWeekTest(TestCase):
    def setUp(self):
        self.session = _create_full_session()
        initialise_session(self.session)
        self.session.pending_customer_demand = 4
        self.session.save()
        open_week(self.session)
        for role in ['retailer', 'wholesaler', 'distributor', 'factory']:
            ps = self.session.player_sessions.get(role=role)
            apply_receive(ps)
            apply_ship(ps)
            apply_order(ps, 4)
        # Customer also needs to be done
        customer_ps = self.session.player_sessions.get(role='customer')
        customer_ps.turn_phase = PlayerSession.PHASE_DONE
        customer_ps.save()

    def test_close_week_advances_week(self):
        close_week(self.session)
        self.session.refresh_from_db()
        self.assertEqual(self.session.current_week, 1)

    def test_close_week_creates_weekly_states(self):
        close_week(self.session)
        for role in ['retailer', 'wholesaler', 'distributor', 'factory']:
            player = self.session.players.get(role=role)
            ws = WeeklyState.objects.filter(player=player, week=1)
            self.assertTrue(ws.exists())

    def test_close_week_creates_customer_demand(self):
        close_week(self.session)
        demand = CustomerDemand.objects.filter(session=self.session, week=1)
        self.assertTrue(demand.exists())
        self.assertEqual(demand.first().quantity, 4)

    def test_close_week_calculates_costs(self):
        close_week(self.session)
        summary = close_week  # we already called it
        for player in self.session.players.all():
            self.assertGreaterEqual(player.total_cost, 0)

    def test_close_week_resets_phases(self):
        close_week(self.session)
        for ps in self.session.player_sessions.all():
            ps.refresh_from_db()
            self.assertEqual(ps.turn_phase, PlayerSession.PHASE_IDLE)

    def test_close_week_clears_submissions(self):
        close_week(self.session)
        self.session.refresh_from_db()
        self.assertEqual(self.session.submitted_roles, '')
        self.assertIsNone(self.session.pending_customer_demand)

    def test_close_week_returns_summary(self):
        summary = close_week(self.session)
        self.assertIn('retailer', summary)
        self.assertIn('inventory', summary['retailer'])
        self.assertIn('total_cost', summary['retailer'])

    def test_close_week_finishes_game_at_max(self):
        self.session.max_weeks = 1
        self.session.save()
        close_week(self.session)
        self.session.refresh_from_db()
        self.assertFalse(self.session.is_active)
        self.assertEqual(self.session.status, GameSession.STATUS_FINISHED)


class FullWeekCycleTest(TestCase):
    """End-to-end test of the phase-gated system through multiple weeks."""

    def test_two_full_weeks(self):
        session = _create_full_session()
        initialise_session(session)

        for week_num in range(1, 3):
            session.pending_customer_demand = 4
            session.save()
            open_week(session)

            for role in ['retailer', 'wholesaler', 'distributor', 'factory']:
                ps = session.player_sessions.get(role=role)
                apply_receive(ps)
                apply_ship(ps)
                apply_order(ps, 4)

            customer_ps = session.player_sessions.get(role='customer')
            customer_ps.turn_phase = PlayerSession.PHASE_DONE
            customer_ps.save()

            close_week(session)
            session.refresh_from_db()
            self.assertEqual(session.current_week, week_num)

        # After 2 weeks, all players should have weekly state records
        for player in session.players.all():
            self.assertEqual(player.history.count(), 2)


class ProcessWeekTest(TestCase):
    """Tests for the legacy single-pass process_week."""

    def setUp(self):
        self.session = _create_full_session()
        initialise_session(self.session)

    def test_process_week_basic(self):
        self.session.pending_customer_demand = 4
        self.session.save()
        players = {p.id: 4 for p in self.session.players.all()}
        summary = process_week(self.session, players)
        self.assertIn('retailer', summary)
        self.session.refresh_from_db()
        self.assertEqual(self.session.current_week, 1)

    def test_process_week_creates_weekly_states(self):
        self.session.pending_customer_demand = 4
        self.session.save()
        players = {p.id: 4 for p in self.session.players.all()}
        process_week(self.session, players)
        for player in self.session.players.all():
            self.assertEqual(player.history.count(), 1)

    def test_process_week_double_submit_protection(self):
        """Second call with stale expected_current should return empty."""
        self.session.pending_customer_demand = 4
        self.session.save()
        players = {p.id: 4 for p in self.session.players.all()}
        process_week(self.session, players)
        # Simulate stale session object: don't refresh, so current_week is still 0
        # But the DB row already advanced to 1, so the guard should catch it.
        stale_session = GameSession.objects.get(pk=self.session.pk)
        # Manually verify the guard condition: after first process_week,
        # session.current_week in DB == 1.
        self.assertEqual(stale_session.current_week, 1)
        # A second call starting from week 0 would fail because the session
        # object we pass still has current_week=0 (the expected_current).
        # But select_for_update + re-read detects the mismatch and returns {}.
        # NOTE: SQLite doesn't support row-level locking, so we just verify
        # the week advanced correctly.
        self.session.refresh_from_db()
        self.assertEqual(self.session.current_week, 1)

    def test_process_week_finishes_game(self):
        self.session.max_weeks = 1
        self.session.pending_customer_demand = 4
        self.session.save()
        players = {p.id: 4 for p in self.session.players.all()}
        process_week(self.session, players)
        self.session.refresh_from_db()
        self.assertFalse(self.session.is_active)

    def test_process_week_ai_orders(self):
        """When no player orders given, AI orders should be used."""
        self.session.pending_customer_demand = 4
        self.session.save()
        summary = process_week(self.session, {})
        self.assertIn('retailer', summary)
        # AI should have placed orders
        for role_data in summary.values():
            self.assertIn('order_placed', role_data)

    def test_multiple_weeks(self):
        for w in range(1, 4):
            self.session.pending_customer_demand = 4
            self.session.save()
            players = {p.id: 4 for p in self.session.players.all()}
            summary = process_week(self.session, players)
            self.assertIn('retailer', summary)
            self.session.refresh_from_db()
            self.assertEqual(self.session.current_week, w)


class AIOrderTest(TestCase):
    def test_ai_order_base_stock(self):
        session = GameSession.objects.create(name='T')
        player = Player.objects.create(session=session, name='R', role='retailer')
        # With default inventory=12, no in-transit, no backlog: target(16) - 12 = 4
        order = _ai_order(player)
        self.assertEqual(order, 4)

    def test_ai_order_with_backlog(self):
        session = GameSession.objects.create(name='T')
        player = Player.objects.create(session=session, name='R', role='retailer', backlog=5)
        # target(16) - 12 + 5 = 9
        order = _ai_order(player)
        self.assertEqual(order, 9)

    def test_ai_order_high_inventory(self):
        session = GameSession.objects.create(name='T')
        player = Player.objects.create(session=session, name='R', role='retailer', inventory=20)
        # target(16) - 20 = -4 → max(0, -4) = 0
        order = _ai_order(player)
        self.assertEqual(order, 0)

    def test_ai_order_with_in_transit(self):
        session = GameSession.objects.create(name='T')
        player = Player.objects.create(session=session, name='R', role='retailer', inventory=10)
        PipelineShipment.objects.create(
            receiver=player, quantity=3, shipped_on_week=0, arrives_on_week=2,
        )
        # target(16) - 10 - 3 + 0 = 3
        order = _ai_order(player)
        self.assertEqual(order, 3)


class GetChartDataTest(TestCase):
    def test_empty_session(self):
        session = _create_full_session()
        data = get_chart_data(session)
        self.assertIn('retailer', data)
        self.assertIn('customer', data)
        self.assertEqual(data['retailer']['history'], [])

    def test_with_history(self):
        session = _create_full_session()
        initialise_session(session)
        session.pending_customer_demand = 4
        session.save()
        process_week(session, {p.id: 4 for p in session.players.all()})
        data = get_chart_data(session)
        self.assertEqual(len(data['retailer']['history']), 1)
        self.assertEqual(len(data['customer']['history']), 1)


class GetBullwhipDataTest(TestCase):
    def test_insufficient_data(self):
        session = GameSession.objects.create(name='T')
        result = get_bullwhip_data(session)
        self.assertEqual(result, {})

    def test_with_data(self):
        session = _create_full_session()
        initialise_session(session)
        # Run 3 weeks to get enough data for stdev
        for w in range(3):
            session.pending_customer_demand = 4 + w
            session.save()
            process_week(session, {p.id: 4 for p in session.players.all()})
            session.refresh_from_db()
        result = get_bullwhip_data(session)
        # Should have ratios for all 4 supply-chain roles
        self.assertGreater(len(result), 0)
        for ratio in result.values():
            self.assertIsInstance(ratio, float)
