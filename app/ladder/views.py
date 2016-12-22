from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from app.ladder.models import Player, MatchPlayer, Match
from dal import autocomplete
from django.db.models import Max, Count, Prefetch, Case, When, F, ExpressionWrapper, FloatField
from django.views.generic import ListView, DetailView


class PlayerList(ListView):
    # those who played at least 1 game
    # TODO make active players manager
    queryset = Player.objects.exclude(name__in=['hoxieloxie'])\
        .filter(matchplayer__isnull=False).distinct()

    def get_context_data(self, **kwargs):
        context = super(PlayerList, self).get_context_data(**kwargs)
        players = context['player_list']

        players = players or Player.objects.all()

        players = players.prefetch_related(Prefetch(
            'matchplayer_set',
            queryset=MatchPlayer.objects.select_related('match'),
            to_attr='matches'
        )).annotate(
            match_count=Count('matchplayer'),
            wins=Count(Case(
                When(
                    matchplayer__team=F('matchplayer__match__winner'), then=1)
                )
            ),
            winrate=ExpressionWrapper(
                F('wins') * Decimal('100') / F('match_count'),
                output_field=FloatField()
            )
        )

        max_vals = players.aggregate(Max('mmr'), Max('score'), Max('ladder_mmr'))
        score_max = max_vals['score__max']
        mmr_max = max_vals['mmr__max']
        ladder_mmr_max = max_vals['ladder_mmr__max']

        matches_max = max(player.match_count for player in players)
        matches_max = max(matches_max, 1)

        for player in players:
            player.score_percent = float(player.score) / score_max * 100
            player.mmr_percent = float(player.mmr) / mmr_max * 100
            player.ladder_mmr_percent = float(player.ladder_mmr) / ladder_mmr_max * 100
            player.matches_percent = float(player.match_count) / matches_max * 100

        context.update({
            'player_list': players,
            'matches_count': Match.objects.count()
        })

        return context


# TODO: inherit PlayersBest and PlayersSuccessful from PlayerList
class PlayersSuccessful(ListView):
    template_name = 'ladder/player_list_score.html'
    # those who played at least 1 game
    # TODO make active players manager
    queryset = Player.objects.exclude(name__in=['hoxieloxie'])\
        .filter(matchplayer__isnull=False).distinct()

    def get_context_data(self, **kwargs):
        context = super(PlayersSuccessful, self).get_context_data(**kwargs)
        players = context['player_list']

        players = players or Player.objects.all()

        players = players.prefetch_related(Prefetch(
            'matchplayer_set',
            queryset=MatchPlayer.objects.select_related('match'),
            to_attr='matches'
        )).annotate(
            match_count=Count('matchplayer'),
            wins=Count(Case(
                When(
                    matchplayer__team=F('matchplayer__match__winner'), then=1)
                )
            ),
            losses=F('match_count') - F('wins'),
        )

        # TODO: something like this:
        #       max_vals = players.aggregate(...).values_list(flat=True)
        #       score_max, mmr_max, ... = max_vals

        max_vals = players.aggregate(Max('wins'), Max('losses'), Max('score'), Max('ladder_mmr'))
        wins_max = max(1, max_vals['wins__max'])
        losses_max = max(1, max_vals['losses__max'])
        score_max = max(1, max_vals['score__max'])
        ladder_mmr_max = max(1, max_vals['ladder_mmr__max'])

        matches_max = max(player.match_count for player in players)
        matches_max = max(1, matches_max)

        for player in players:
            player.wins_percent = float(player.wins) / wins_max * 100
            player.loss_percent = float(player.losses) / losses_max * 100
            player.score_percent = float(player.score) / score_max * 100
            player.ladder_mmr_percent = float(player.ladder_mmr) / ladder_mmr_max * 100
            player.matches_percent = float(player.match_count) / matches_max * 100

        context.update({
            'player_list': players,
            'matches_count': Match.objects.count()
        })

        return context


# TODO: inherit PlayerOverview, PlayerAllies etc from PlayerDetail
class PlayerOverview(DetailView):
    model = Player
    context_object_name = 'player'
    slug_field = 'slug__iexact'

    def get_context_data(self, **kwargs):
        context = super(PlayerOverview, self).get_context_data(**kwargs)

        player = self.object

        matches = player.matchplayer_set.select_related('match')
        wins = sum(1 if m.match.winner == m.team else 0 for m in matches)
        losses = len(matches) - wins

        win_percent = 0
        if matches:
            win_percent = float(wins) / len(matches) * 100

        score_changes = player.scorechange_set.select_related(
            'match', 'match__match',
            'match__match__balance', 'match__match__balance__result'
        )

        # calc score history
        score = mmr = 0
        for scoreChange in reversed(score_changes):
            score += scoreChange.amount
            mmr += scoreChange.mmr_change

            scoreChange.score = score
            scoreChange.mmr = mmr

        context.update({
            'wins': wins,
            'losses': losses,
            'winrate': win_percent,
            'match_list': matches,
            'score_changes': score_changes,
        })

        return context


# TODO: inherit from ListView and SingleObjectMixin
class PlayerAllies(DetailView):
    model = Player
    context_object_name = 'player'
    slug_field = 'slug__iexact'
    template_name = 'ladder/player_teammates.html'

    def get_context_data(self, **kwargs):
        context = super(PlayerAllies, self).get_context_data(**kwargs)

        player = self.object

        matches = player.matchplayer_set.select_related(
            'match'
        ).prefetch_related(
            Prefetch('match__matchplayer_set',
                     queryset=MatchPlayer.objects.select_related('player'))
        ).prefetch_related('match__matchplayer_set__scorechange_set')

        wins = sum(1 if m.match.winner == m.team else 0 for m in matches)
        losses = len(matches) - wins

        win_percent = 0
        if matches:
            win_percent = float(wins) / len(matches) * 100

        # gather initial teammate stats
        teammates = defaultdict(lambda: defaultdict(int))
        for matchPlayer in matches:
            match = matchPlayer.match
            for mp in match.matchplayer_set.all():  # all players for this match
                if mp.player == player:
                    continue  # this is us, not a teammate

                if mp.team == matchPlayer.team:
                    teammate = teammates[mp.player.name]
                    teammate['match_count'] += 1
                    teammate['wins'] += 1 if match.winner == mp.team else 0
                    teammate['mmr_change'] += mp.scorechange_set.first().mmr_change
                    teammate['score_change'] += mp.scorechange_set.first().amount

                    teammate['last_played'] = mp.match.date if not teammate['last_played'] \
                        else max(mp.match.date, teammate['last_played'])

        # calc additional teammate stats
        matches_max = max(t['match_count'] for t in teammates.values())
        for name, teammate in teammates.iteritems():
            teammate['name'] = name
            teammate['winrate'] = float(teammate['wins']) / teammate['match_count'] * 100
            teammate['matches_percent'] = float(teammate['match_count']) / matches_max * 100

        teammates = sorted(teammates.values(), key=lambda x: -x['mmr_change'])

        context.update({
            'wins': wins,
            'losses': losses,
            'winrate': win_percent,
            'teammates': teammates,
        })

        return context


class PlayerAutocomplete(autocomplete.Select2QuerySetView):
    queryset = Player.objects.order_by('name')