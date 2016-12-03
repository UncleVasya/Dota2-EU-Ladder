from app.ladder.models import Player
from dal import autocomplete
from django.db.models import Max
from django.views.generic import ListView, DetailView


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


class PlayerOverview(DetailView):
    model = Player
    context_object_name = 'player'
    slug_field = 'slug__iexact'

    def get_context_data(self, **kwargs):
        context = super(PlayerOverview, self).get_context_data(**kwargs)

        player = self.object

        matches = player.matchplayer_set.all()
        wins = sum(1 if m.match.winner == m.team else 0 for m in matches)
        losses = len(matches) - wins
        win_percent = 0
        if matches:
            win_percent = float(wins) / len(matches) * 100

        context.update({
            'wins': wins,
            'losses': losses,
            'winrate': win_percent,
            'match_list': matches,
        })

        return context


class PlayerAutocomplete(autocomplete.Select2QuerySetView):
    model = Player
