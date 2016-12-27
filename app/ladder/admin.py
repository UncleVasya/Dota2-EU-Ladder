from django import forms
from app.ladder.models import Player, Match, MatchPlayer, ScoreChange
from django.contrib import admin
from django.db.models import Prefetch
from dal import autocomplete


class PlayerAdmin(admin.ModelAdmin):
    model = Player

    fieldsets = [
        (None, {'fields': ['name', 'dota_mmr', 'dota_id', 'rank_ladder_mmr']}),
    ]
    readonly_fields = ('rank_ladder_mmr',)

    list_display = ('name', 'rank_ladder_mmr', 'score', 'dota_mmr')

    def save_model(self, request, obj, form, change):
        super(PlayerAdmin, self).save_model(request, obj, form, change)

        Player.objects.update_ranks()


class MatchPlayerInlineForm(forms.ModelForm):
    player = forms.ModelChoiceField(
        queryset=Player.objects.all(),
        widget=autocomplete.ModelSelect2(url='ladder:player-autocomplete')
    )

    class Meta:
        model = Player
        fields = ('__all__')


class MatchPlayerInline(admin.TabularInline):
    model = MatchPlayer
    form = MatchPlayerInlineForm
    min_num = 10
    max_num = 10


class MatchAdmin(admin.ModelAdmin):
    model = Match

    fieldsets = [
        (None, {'fields': ['date', 'winner']}),
    ]
    readonly_fields = ['date']

    inlines = (MatchPlayerInline, )

    list_display = ('date', )


class ScoreChangeAdminForm(forms.ModelForm):
    player = forms.ModelChoiceField(
        queryset=Player.objects.all(),
        widget=autocomplete.ModelSelect2(url='ladder:player-autocomplete')
    )


class ScoreChangeAdmin(admin.ModelAdmin):
    model = ScoreChange
    form = ScoreChangeAdminForm

    fieldsets = [
        (None, {'fields': ['player', 'mmr_change', 'info']}),
    ]

    list_display = ('date', 'player', 'mmr_change', 'info')

    def save_model(self, *args, **kwargs):
        super(ScoreChangeAdmin, self).save_model(*args, **kwargs)

        Player.objects.update_ranks()


admin.site.register(Player, PlayerAdmin)
admin.site.register(ScoreChange, ScoreChangeAdmin)

# TODO: manual match input can be used to record PlayerDraft games;
# TODO: atm we don't use it
admin.site.register(Match, MatchAdmin)