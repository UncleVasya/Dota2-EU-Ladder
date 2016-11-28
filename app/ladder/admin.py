from app.ladder.models import Player
from django.contrib import admin


class PlayerAdmin(admin.ModelAdmin):
    model = Player

    fieldsets = [
        (None, {'fields': ['name', 'mmr', 'score', 'rank', 'dota_id']}),
    ]
    readonly_fields = ('rank', 'dota_id')

    list_display = ('name', 'rank', 'score', 'mmr')


admin.site.register(Player, PlayerAdmin)