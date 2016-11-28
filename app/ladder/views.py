from app.ladder.models import Player
from dal import autocomplete
from django.db.models import Max
from django.views.generic import ListView


class PlayerList(ListView):
    model = Player

    def get_context_data(self, **kwargs):
        context = super(PlayerList, self).get_context_data(**kwargs)
        players = context['player_list']

        max_vals = players.aggregate(Max('mmr'), Max('score'))
        score_max = max_vals['score__max']
        mmr_max = max_vals['mmr__max']

        for player in players:
            player.score_percent = float(player.score) / score_max * 100
            player.mmr_percent = float(player.mmr) / mmr_max * 100

        context.update({
            'player_list': players,
        })

        return context


class PlayerAutocomplete(autocomplete.Select2QuerySetView):
    model = Player
