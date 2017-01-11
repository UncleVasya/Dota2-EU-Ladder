from django import forms
from app.ladder.models import Player, Match, MatchPlayer, ScoreChange
from django.contrib import admin
from django.db.models import Prefetch
from dal import autocomplete


class BlacklistInlineForm(forms.ModelForm):
    from_player = forms.ModelChoiceField(
        queryset=Player.objects.all(),
        widget=autocomplete.ModelSelect2(url='ladder:player-autocomplete')
    )
    to_player = forms.ModelChoiceField(
        queryset=Player.objects.all(),
        widget=autocomplete.ModelSelect2(url='ladder:player-autocomplete')
    )

    class Meta:
        model = Player.blacklist.through
        fields = ('__all__')


class BlacklistInline(admin.TabularInline):
    model = Player.blacklist.through
    form = BlacklistInlineForm
    fk_name = 'from_player'


class BlacklistedByInline(admin.TabularInline):
    model = Player.blacklist.through
    form = BlacklistInlineForm
    fk_name = 'to_player'


class PlayerAdmin(admin.ModelAdmin):
    model = Player

    fieldsets = [
        (None, {'fields': ['name', 'dota_mmr', 'dota_id', 'voice_issues', 'bot_access']}),
        (None, {'fields': ['ladder_mmr', 'score']}),
        (None, {'fields': ['rank_ladder_mmr', 'rank_score']}),
    ]
    readonly_fields = ('ladder_mmr', 'score', 'rank_ladder_mmr', 'rank_score')

    list_display = ('name', 'rank_ladder_mmr', 'score', 'dota_mmr', 'dota_id')
    search_fields = ('=name',)

    inlines = (BlacklistInline, BlacklistedByInline)

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
admin.site.register(Match, MatchAdmin)

admin.site.register(Player.blacklist.through)