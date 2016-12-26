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


# This view is used as a base class for other views,
# it is not used directly
class PlayerDetail(DetailView):
    model = Player
    context_object_name = 'player'
    slug_field = 'slug__iexact'

    def get_object(self, queryset=None):
        player = super(PlayerDetail, self).get_object(queryset)
        player.matches = player.matchplayer_set.select_related('match')
        return player

    def get_context_data(self, **kwargs):
        context = super(PlayerDetail, self).get_context_data(**kwargs)

        matches = context['player'].matches
        wins = sum(1 if m.match.winner == m.team else 0 for m in matches)
        losses = len(matches) - wins

        win_percent = 0
        if matches:
            win_percent = float(wins) / len(matches) * 100

        context.update({
            'wins': wins,
            'losses': losses,
            'winrate': win_percent,
        })
        return context

    def add_matches_data(self):
        player = self.object

        player.matches = player.matchplayer_set.select_related(
            'match'
        ).prefetch_related(
            Prefetch('match__matchplayer_set',
                     queryset=MatchPlayer.objects.select_related('player'))
        ).prefetch_related('scorechange_set')

    def score_history(self):
        player = self.object
        score_changes = player.scorechange_set.select_related(
            'match', 'match__match',
            'match__match__balance', 'match__match__balance__result'
        )

        score = mmr = 0
        for scoreChange in reversed(score_changes):
            score += scoreChange.score_change
            mmr += scoreChange.mmr_change

            scoreChange.score = score
            scoreChange.mmr = mmr

        return score_changes

    def teammates_stats(self, matches_min=3, opponents=False):
        player = self.object

        # gather initial teammate stats
        teammates = defaultdict(lambda: defaultdict(int))
        for matchPlayer in player.matches:
            match = matchPlayer.match
            changes = matchPlayer.scorechange_set.all()[0]
            for mp in match.matchplayer_set.all():  # all players for this match
                if mp.player == player or \
                   mp.team == matchPlayer.team and opponents or \
                   mp.team != matchPlayer.team and not opponents:
                    continue  # this is us, or not our teammate/opponent

                teammate = teammates[mp.player.name]
                teammate['match_count'] += 1
                teammate['wins'] += 1 if match.winner == matchPlayer.team else 0
                teammate['mmr_change'] += changes.mmr_change
                teammate['score_change'] += changes.score_change

                teammate['last_played'] = mp.match.date if not teammate['last_played'] \
                    else max(mp.match.date, teammate['last_played'])

        # calc additional teammate stats
        matches_max = max(t['match_count'] for t in teammates.values())
        for name, teammate in teammates.iteritems():
            teammate['name'] = name
            teammate['winrate'] = float(teammate['wins']) / teammate['match_count'] * 100
            teammate['matches_percent'] = float(teammate['match_count']) / matches_max * 100

        teammates = sorted(teammates.values(), key=lambda x: -x['mmr_change'])
        return [t for t in teammates if t['match_count'] >= matches_min]


class PlayerOverview(PlayerDetail):
    template_name = 'ladder/player_overview.html'

    def get_context_data(self, **kwargs):
        self.add_matches_data()
        context = super(PlayerOverview, self).get_context_data(**kwargs)
        context.update({
            'score_changes': self.score_history()[:10],
            'teammates': self.teammates_stats()[:5],
            'opponents': reversed(self.teammates_stats(opponents=True)[-5:]),
        })
        return context


class PlayerScores(PlayerDetail):
    template_name = 'ladder/player_scores.html'

    def get_context_data(self, **kwargs):
        context = super(PlayerScores, self).get_context_data(**kwargs)
        context.update({
            'score_changes': self.score_history(),
        })
        return context


class PlayerTeammates(PlayerDetail):
    template_name = 'ladder/player_teammates.html'

    def get_context_data(self, **kwargs):
        self.add_matches_data()
        context = super(PlayerTeammates, self).get_context_data(**kwargs)
        context.update({
            'teammates': self.teammates_stats(),
        })
        return context


class PlayerOpponents(PlayerDetail):
    template_name = 'ladder/player_opponents.html'

    def get_context_data(self, **kwargs):
        self.add_matches_data()
        context = super(PlayerOpponents, self).get_context_data(**kwargs)
        context.update({
            'opponents': reversed(self.teammates_stats(opponents=True)),
        })
        return context


class PlayerAutocomplete(autocomplete.Select2QuerySetView):
    queryset = Player.objects.order_by('name')