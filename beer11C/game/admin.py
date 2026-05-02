from django.contrib import admin
from .models import (
    GameSession, Player, WeeklyState,
    PipelineOrder, PipelineShipment, CustomerDemand,
    PlayerSession, LobbyMessage,
)


@admin.register(GameSession)
class GameSessionAdmin(admin.ModelAdmin):
    list_display  = ('id', 'name', 'status', 'visibility_mode', 'current_week', 'max_weeks', 'created_by', 'created_at')
    list_filter   = ('status', 'visibility_mode')
    search_fields = ('name', 'created_by__username')
    readonly_fields = ('created_at',)
    ordering = ('-created_at',)


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display  = ('id', 'session', 'role', 'inventory', 'backlog', 'total_cost')
    list_filter   = ('role',)
    search_fields = ('session__name', 'name')


@admin.register(PlayerSession)
class PlayerSessionAdmin(admin.ModelAdmin):
    list_display  = ('id', 'game_session', 'role', 'name', 'user', 'is_connected', 'is_ai', 'turn_phase')
    list_filter   = ('role', 'is_connected', 'is_ai', 'turn_phase')
    search_fields = ('game_session__name', 'name', 'user__username')
    readonly_fields = ('token',)


@admin.register(WeeklyState)
class WeeklyStateAdmin(admin.ModelAdmin):
    list_display  = ('id', 'player', 'week', 'inventory', 'backlog', 'cost_this_week')
    list_filter   = ('week',)
    search_fields = ('player__session__name', 'player__role')


@admin.register(CustomerDemand)
class CustomerDemandAdmin(admin.ModelAdmin):
    list_display  = ('id', 'session', 'week', 'quantity')
    list_filter   = ('week',)
    search_fields = ('session__name',)


@admin.register(PipelineOrder)
class PipelineOrderAdmin(admin.ModelAdmin):
    list_display  = ('id', 'sender', 'quantity', 'placed_on_week', 'arrives_on_week', 'fulfilled')
    list_filter   = ('fulfilled',)


@admin.register(PipelineShipment)
class PipelineShipmentAdmin(admin.ModelAdmin):
    list_display  = ('id', 'receiver', 'quantity', 'shipped_on_week', 'arrives_on_week', 'delivered')
    list_filter   = ('delivered',)


@admin.register(LobbyMessage)
class LobbyMessageAdmin(admin.ModelAdmin):
    list_display  = ('id', 'game_session', 'author_name', 'author_role', 'body', 'created_at')
    search_fields = ('author_name', 'body', 'game_session__name')
    readonly_fields = ('created_at',)
