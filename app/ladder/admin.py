from django import forms
from app.ladder.models import Player, Match, MatchPlayer
from django.contrib import admin
from django.db.models import Prefetch
from dal import autocomplete


class PlayerAdmin(admin.ModelAdmin):
    model = Player

    fieldsets = [
        (None, {'fields': ['name', 'mmr', 'score', 'rank', 'dota_id']}),
    ]
    readonly_fields = ('rank', 'dota_id')

    list_display = ('name', 'rank', 'score', 'mmr')

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

    def get_queryset(self, request):  # performance optimisation
        qs = super(MatchAdmin, self).get_queryset(request)
        return qs.prefetch_related(Prefetch('players'))

    def save_related(self, request, form, formsets, change):
        super(MatchAdmin, self).save_related(request, form, formsets, change)

        Player.objects.update_ranks()


admin.site.register(Player, PlayerAdmin)
admin.site.register(Match, MatchAdmin)