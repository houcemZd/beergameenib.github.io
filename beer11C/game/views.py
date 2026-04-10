import json
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.contrib.auth import login, logout
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from .models import (
    GameSession, Player, CustomerDemand,
    PlayerSession, PipelineShipment, PipelineOrder,
)
from .services import initialise_session, process_week, get_chart_data, get_bullwhip_data, _ai_order

CHAIN_ORDER  = {'retailer': 1, 'wholesaler': 2, 'distributor': 3, 'factory': 4}
ROLE_EMOJIS  = {
    'customer':    '👤',
    'retailer':    '🛒',
    'wholesaler':  '🏪',
    'distributor': '🚚',
    'factory':     '🏭',
}
SUPPLY_ROLES = ['retailer', 'wholesaler', 'distributor', 'factory']
ALL_ROLES    = ['customer', 'retailer', 'wholesaler', 'distributor', 'factory']


def _sorted_players(players):
    return sorted(players, key=lambda p: CHAIN_ORDER.get(p.role, 99))


def _build_pipeline_data(players, current_week):
    data = []
    for player in players:
        upstream = player.get_upstream()
        for s in PipelineShipment.objects.filter(
            receiver=player, delivered=False
        ).order_by('arrives_on_week'):
            data.append({
                'from':       upstream.role if upstream else player.role,
                'to':         player.role,
                'qty':        s.quantity,
                'arrives':    s.arrives_on_week,
                'weeks_away': max(0, s.arrives_on_week - current_week),
                'type':       'ship' if player.role in ('distributor', 'wholesaler') else 'truck',
            })
        if player.role != 'factory':
            for o in PipelineOrder.objects.filter(
                sender=player, fulfilled=False
            ).order_by('arrives_on_week'):
                data.append({
                    'from':       player.role,
                    'to':         upstream.role if upstream else player.role,
                    'qty':        o.quantity,
                    'arrives':    o.arrives_on_week,
                    'weeks_away': max(0, o.arrives_on_week - current_week),
                    'type':       'order',
                })
    return data


# ── Auth ──────────────────────────────────────────────────────────────────────
def register(request):
    if request.user.is_authenticated:
        return redirect('home')
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('home')
    else:
        form = UserCreationForm()
    return render(request, 'game/register.html', {'form': form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect(request.GET.get('next', 'home'))
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            return redirect(request.POST.get('next') or 'home')
    else:
        form = AuthenticationForm()
    return render(request, 'game/login.html', {
        'form': form,
        'next': request.GET.get('next', ''),
    })


def logout_view(request):
    logout(request)
    return redirect('login')


# ── Home ──────────────────────────────────────────────────────────────────────
@login_required
def home(request):
    all_sessions = GameSession.objects.select_related('created_by').order_by('-created_at')

    # Open multiplayer lobbies anyone can join
    lobby_sessions = [
        s for s in all_sessions
        if s.status == GameSession.STATUS_LOBBY and s.player_sessions.exists()
    ]
    # Active multiplayer games (spectate/observe)
    active_sessions = [
        s for s in all_sessions
        if s.status == GameSession.STATUS_PLAYING and s.player_sessions.exists()
    ]
    # Sessions created by this user (solo or theirs)
    my_sessions = [
        s for s in all_sessions
        if s.created_by == request.user
    ]

    stats = {
        'total':    all_sessions.count(),
        'active':   len(active_sessions),
        'lobby':    len(lobby_sessions),
        'finished': all_sessions.filter(status=GameSession.STATUS_FINISHED).count(),
    }
    weeks_options = [(12,'court'), (20,'standard'), (30,'long'), (40,'étendu')]

    return render(request, 'game/home.html', {
        'lobby_sessions':  lobby_sessions,
        'active_sessions': active_sessions,
        'my_sessions':     my_sessions,
        'stats':           stats,
        'weeks_options':   weeks_options,
    })


# ── New game ──────────────────────────────────────────────────────────────────
@login_required
def new_game(request):
    if request.method == 'POST':
        name      = request.POST.get('name', 'Beer Game').strip() or 'Beer Game'
        max_weeks = int(request.POST.get('max_weeks', 20))
        max_weeks = max(12, min(40, max_weeks))
        mode      = request.POST.get('mode', 'single')

        session = GameSession.objects.create(
            name=name,
            max_weeks=max_weeks,
            status=GameSession.STATUS_LOBBY,
            created_by=request.user,          # ← NEW: track creator
        )
        for player_name, role in [
            ('Retailer','retailer'), ('Wholesaler','wholesaler'),
            ('Distributor','distributor'), ('Factory','factory'),
        ]:
            Player.objects.create(session=session, name=player_name, role=role)

        if mode == 'multi':
            for role in ALL_ROLES:
                PlayerSession.objects.create(game_session=session, role=role)

        return redirect('game_init', session_id=session.id)

    return render(request, 'game/new_game.html')

# ── Initialisation step ───────────────────────────────────────────────────────
@login_required
def game_init(request, session_id):
    """
    Step 2: configure initial state before the game starts.
    Player sets: initial inventory, orders placed (pipeline), incoming orders (pipeline).
    """
    session = get_object_or_404(GameSession, id=session_id)

    if request.method == 'POST':
        # Read initial parameters from form
        init_inventory     = max(0, int(request.POST.get('init_inventory', 12)))
        init_orders_placed = max(0, int(request.POST.get('init_orders_placed', 4)))
        init_incoming      = max(0, int(request.POST.get('init_incoming', 4)))
        holding_cost       = float(request.POST.get('holding_cost', 0.5))
        backlog_cost       = float(request.POST.get('backlog_cost', 1.0))

        # Apply inventory + costs to all players
        for player in session.players.all():
            player.inventory    = init_inventory
            player.holding_cost = holding_cost
            player.backlog_cost = backlog_cost
            player.save()

        # Call initialise_session with custom pipeline values
        initialise_session(
            session,
            init_orders_placed=init_orders_placed,
            init_incoming=init_incoming,
        )

        # Transition to correct status
        has_player_sessions = session.player_sessions.exists()
        if has_player_sessions:
            session.status = GameSession.STATUS_LOBBY
            session.save(update_fields=['status'])
            return redirect('lobby', session_id=session.id)
        else:
            session.status = GameSession.STATUS_PLAYING
            session.save(update_fields=['status'])
            return redirect('dashboard', session_id=session.id)

    return render(request, 'game/game_init.html', {'session': session})


# ── Lobby ─────────────────────────────────────────────────────────────────────
@login_required
def lobby(request, session_id):
    session = get_object_or_404(GameSession, id=session_id)
    role_links = []
    for ps in sorted(session.player_sessions.all(), key=lambda p: ALL_ROLES.index(p.role)):
        join_url = request.build_absolute_uri(f'/join/{ps.token}/')
        role_links.append({
            'role':     ps.role,
            'emoji':    ROLE_EMOJIS.get(ps.role, ''),
            'token':    ps.token,
            'url':      join_url,
        })

    # Initial board state for the preview — separate orders vs shipments
    initial_players  = _sorted_players(session.players.all())
    first_order = PipelineOrder.objects.filter(
        sender__session=session
    ).first()
    first_ship  = PipelineShipment.objects.filter(
        receiver__session=session
    ).first()
    initial_orders   = first_order.quantity if first_order else 4
    initial_ships    = first_ship.quantity  if first_ship  else 4
    initial_inv      = initial_players[0].inventory if initial_players else 12

    return render(request, 'game/lobby.html', {
        'session':         session,
        'role_links':      role_links,
        'initial_players': initial_players,
        'initial_orders':  initial_orders,
        'initial_ships':   initial_ships,
        'initial_inv':     initial_inv,
    })


# ── Lobby status API (polled by lobby.html every 2s) ──────────────────────────
@login_required
def lobby_status(request, session_id):
    session = get_object_or_404(GameSession, id=session_id)
    player_sessions = list(session.player_sessions.all())
    joined      = [ps.role for ps in player_sessions if ps.name]
    connected   = [ps.role for ps in player_sessions if ps.is_connected]
    names       = {ps.role: ps.name for ps in player_sessions if ps.name}
    game_started = session.status == GameSession.STATUS_PLAYING
    return JsonResponse({
        'joined':       joined,
        'connected':    connected,
        'names':        names,
        'game_started': game_started,
        'status':       session.status,
        'current_week': session.current_week,
        'max_weeks':    session.max_weeks,
    })


# ── Join ──────────────────────────────────────────────────────────────────────
@login_required
def join_game(request, token):
    """
    Players join via a role-specific token link (shared by the session creator).
    If the user is authenticated, we link the PlayerSession to their account.
    """
    ps      = get_object_or_404(PlayerSession, token=token)
    session = ps.game_session

    # If this role is already claimed by a different user, block it
    if ps.user and request.user.is_authenticated and ps.user != request.user:
        return render(request, 'game/join_taken.html', {
            'session': session,
            'role':    ps.role,
            'claimed_by': ps.user.get_full_name() or ps.user.username,
        })

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()[:50]

        # Use account name as default if logged in and no name given
        if not name and request.user.is_authenticated:
            name = request.user.first_name or request.user.username

        update_fields = []
        if name:
            ps.name = name
            update_fields.append('name')

        # Claim role for this user
        if request.user.is_authenticated and not ps.user:
            ps.user = request.user
            update_fields.append('user')

        if update_fields:
            ps.save(update_fields=update_fields)

        request.session['player_token'] = token
        if ps.role == 'customer':
            return redirect(f"{reverse('customer_play', args=[session.id])}?token={token}")
        return redirect(f"{reverse('play', args=[session.id])}?token={token}")

    # Pre-fill name from account
    default_name = ''
    if request.user.is_authenticated:
        default_name = request.user.first_name or request.user.username

    return render(request, 'game/join.html', {
        'session':      session,
        'role':         ps.role,
        'emoji':        ROLE_EMOJIS.get(ps.role, ''),
        'token':        token,
        'default_name': default_name,
        'already_claimed': bool(ps.user and ps.user == request.user),
    })

# ── Multiplayer supply-chain player ───────────────────────────────────────────
@login_required
def play(request, session_id):
    # Accept token from URL param (cross-device) or session cookie (same-device)
    token = request.GET.get('token') or request.session.get('player_token')
    if not token:
        return redirect('home')
    ps = get_object_or_404(PlayerSession, token=token, game_session_id=session_id)
    session = ps.game_session
    # Store in session cookie so refreshes on same device stay authenticated
    request.session['player_token'] = token
    return render(request, 'game/play.html', {
        'session': session, 'player_session': ps,
        'role': ps.role, 'emoji': ROLE_EMOJIS.get(ps.role, ''),
        'token': token, 'roles': ALL_ROLES,
        'ws_path': f"/ws/game/{session_id}/{token}/",
    })


# ── Customer play (WebSocket, real-time) ──────────────────────────────────────
@login_required
def customer_play(request, session_id):
    token = request.GET.get('token') or request.session.get('player_token')
    if not token:
        return redirect('home')
    ps = get_object_or_404(PlayerSession, token=token, game_session_id=session_id, role='customer')
    session = ps.game_session
    request.session['player_token'] = token
    demand_history = list(CustomerDemand.objects.filter(session=session).order_by('week'))
    return render(request, 'game/customer_play.html', {
        'session': session, 'player_session': ps,
        'token': token,
        'demand_history': demand_history,
        'roles': ALL_ROLES,
        'ws_path': f"/ws/game/{session_id}/{token}/",
    })


# ── Single-player dashboard ───────────────────────────────────────────────────
@login_required
def dashboard(request, session_id):
    session = get_object_or_404(GameSession, id=session_id)
    players = _sorted_players(session.players.prefetch_related('history').all())

    chart_data    = json.dumps(get_chart_data(session))
    pipeline_data = json.dumps(_build_pipeline_data(players, session.current_week))

    last_week_states = {}
    if session.current_week > 0:
        for player in players:
            for state in player.history.all():
                if state.week == session.current_week:
                    last_week_states[player.role] = state
                    break

    # Last customer demand (for display + pre-filling next turn form)
    last_demand = CustomerDemand.objects.filter(
        session=session, week=session.current_week
    ).first()

    # AI-suggested orders for each player (shown as form defaults when no manual value)
    ai_orders = {player.id: _ai_order(player) for player in players} if not session.is_finished else {}

    return render(request, 'game/dashboard.html', {
        'session':          session,
        'players':          players,
        'chart_data':       chart_data,
        'pipeline_data':    pipeline_data,
        'last_week_states': last_week_states,
        'weeks_range':      range(1, session.current_week + 1),
        'roles':            SUPPLY_ROLES,
        'last_demand':      last_demand,
        'ai_orders':        ai_orders,
    })


# ── Single-player next turn (customer demand entered in form) ─────────────────
@require_POST
@login_required
def next_turn(request, session_id):
    session = get_object_or_404(GameSession, id=session_id)
    if not session.is_active or session.is_finished:
        return redirect('dashboard', session_id=session_id)

    # Customer demand (required)
    try:
        customer_qty = max(0, int(request.POST.get('customer_demand', '').strip()))
    except (ValueError, TypeError):
        customer_qty = 4

    # Store pending demand on the session so process_week can read it inside
    # the atomic transaction (select_for_update re-fetches, so we pre-save here).
    session.pending_customer_demand = customer_qty
    session.save(update_fields=['pending_customer_demand'])

    # Supply chain orders (only roles the user explicitly supplied)
    player_orders = {}
    for player in session.players.all():
        key = f'order_{player.id}'
        val = request.POST.get(key, '').strip()
        if val:
            try:
                player_orders[player.id] = max(0, int(val))
            except (ValueError, TypeError):
                pass

    process_week(session, player_orders)
    return redirect('dashboard', session_id=session_id)


# ── Per-role client view (read-only) ─────────────────────────────────────────
@login_required
def client_view(request, session_id, role):
    if role not in CHAIN_ORDER:
        return redirect('dashboard', session_id=session_id)
    session = get_object_or_404(GameSession, id=session_id)
    player  = get_object_or_404(Player, session=session, role=role)
    upstream = player.get_upstream()
    incoming = list(PipelineShipment.objects.filter(
        receiver=player, delivered=False).order_by('arrives_on_week').values('quantity','arrives_on_week'))
    outgoing = []
    if role != 'factory':
        outgoing = list(PipelineOrder.objects.filter(
            sender=player, fulfilled=False).order_by('arrives_on_week').values('quantity','arrives_on_week'))
    history = list(player.history.order_by('week').values(
        'week','inventory','backlog','order_placed',
        'shipment_received','cost_this_week','cumulative_cost'))
    return render(request, 'game/client_view.html', {
        'session': session, 'player': player, 'role': role,
        'emoji': ROLE_EMOJIS.get(role,''), 'incoming': incoming, 'outgoing': outgoing,
        'history': json.dumps(history), 'roles': SUPPLY_ROLES,
        'role_emojis': ROLE_EMOJIS,
    })


# ── Customer view (single-player read/overview) ───────────────────────────────
@login_required
def customer_view(request, session_id):
    """Single-player customer overview — shows demand history and retailer state."""
    session  = get_object_or_404(GameSession, id=session_id)
    retailer = session.players.filter(role='retailer').first()
    demand_history = list(CustomerDemand.objects.filter(session=session).order_by('week'))
    return render(request, 'game/customer_view.html', {
        'session':         session,
        'retailer':        retailer,
        'demand_history':  demand_history,
        'retailer_inv':    retailer.inventory if retailer else 0,
        'retailer_backlog':retailer.backlog if retailer else 0,
        'total_demand':    sum(d.quantity for d in demand_history),
        'avg_demand':      round(sum(d.quantity for d in demand_history) / max(len(demand_history), 1), 1),
        'demand_history_json': json.dumps([{'week': d.week, 'qty': d.quantity} for d in demand_history]),
        'retailer_history_json': json.dumps(list(retailer.history.order_by('week').values('week','inventory','backlog')) if retailer else []),
        'demand_pattern':  request.session.get(f'demand_pattern_{session_id}', 'live'),
        'presets':         [2, 4, 6, 8, 10, 12],
        'next_week':       session.current_week + 1,
        'scheduled_demand': 0,
        'current_override': None,
        'overridden':       False,
    })



@require_POST
@login_required
def reset_game(request, session_id):
    get_object_or_404(GameSession, id=session_id).delete()
    return redirect('home')


# ── Delete via GET with confirmation token (for simple link-based delete) ────
@login_required
def delete_session(request, session_id):
    """GET: confirm page. POST: delete."""
    if request.method == 'POST':
        get_object_or_404(GameSession, id=session_id).delete()
        return redirect('home')
    session = get_object_or_404(GameSession, id=session_id)
    return render(request, 'game/home.html', {
        'sessions': GameSession.objects.order_by('-created_at'),
        'confirm_delete': session,
    })


# ── Results ───────────────────────────────────────────────────────────────────
@login_required
def results(request, session_id):
    session    = get_object_or_404(GameSession, id=session_id)
    players    = _sorted_players(session.players.prefetch_related('history').all())
    chart_data = json.dumps(get_chart_data(session))
    bullwhip   = get_bullwhip_data(session)
    total_cost = sum(p.total_cost for p in players)
    demand_history = list(CustomerDemand.objects.filter(session=session).order_by('week'))
    winner_role = min(players, key=lambda p: p.total_cost).role if players else None
    # Use max(actual max ratio, 5) so the bar scale is consistent across sessions
    # and the ratio=1 baseline marker always appears at ≤20% of the track width.
    bullwhip_max = max(max(bullwhip.values(), default=1), 5) if bullwhip else 5
    return render(request, 'game/results.html', {
        'session': session, 'players': players,
        'chart_data': chart_data, 'bullwhip': bullwhip,
        'total_cost': total_cost, 'demand_history': demand_history,
        'winner_role': winner_role,
        'bullwhip_max': bullwhip_max,
    })


# ── Chart API ─────────────────────────────────────────────────────────────────
@login_required
def chart_data_api(request, session_id):
    return JsonResponse(get_chart_data(get_object_or_404(GameSession, id=session_id)))



