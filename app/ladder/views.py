from collections import defaultdict
from decimal import Decimal
from datetime import timedelta
from django.core.cache import cache
import itertools
from app.ladder.models import Player, MatchPlayer, Match, LadderSettings
from dal import autocomplete
from django.db.models import Max, Count, Prefetch, Case, When, F, ExpressionWrapper, FloatField, Avg
from django.utils.datetime_safe import datetime
from django.views.generic import ListView, DetailView, TemplateView
from pure_pagination import Paginator


class PlayerList(ListView):
    model = Player

    def get_queryset(self):
        qs = super(PlayerList, self).get_queryset()

        season = LadderSettings.get_solo().current_season
        qs = qs.filter(matchplayer__match__season=season).distinct()\
            .prefetch_related(Prefetch(
                'matchplayer_set',
                queryset=MatchPlayer.objects.select_related('match'),
                to_attr='matches'
            ))
        return qs

    def get_context_data(self, **kwargs):
        context = super(PlayerList, self).get_context_data(**kwargs)
        players = context['player_list']

        players = players.annotate(
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

        if not players:
            # no games this season yet, nothing to calc
            return context

        max_vals = players.aggregate(Max('dota_mmr'), Max('score'), Max('ladder_mmr'))
        score_max = max_vals['score__max']
        dota_mmr_max = max_vals['dota_mmr__max']
        ladder_mmr_max = max_vals['ladder_mmr__max']

        matches_max = max(player.match_count for player in players)

        for player in players:
            player.score_percent = float(player.score) / score_max * 100
            player.dota_mmr_percent = float(player.dota_mmr) / dota_mmr_max * 100
            player.ladder_mmr_percent = float(player.ladder_mmr) / ladder_mmr_max * 100
            player.matches_percent = float(player.match_count) / matches_max * 100

        context.update({
            'player_list': players,
        })

        return context


# TODO: inherit PlayersBest and PlayersSuccessful from PlayerList
class PlayersSuccessful(ListView):
    model = Player
    template_name = 'ladder/player_list_score.html'

    def get_queryset(self):
        qs = super(PlayersSuccessful, self).get_queryset()

        season = LadderSettings.get_solo().current_season
        qs = qs.filter(matchplayer__match__season=season).distinct()\
            .prefetch_related(Prefetch(
                'matchplayer_set',
                queryset=MatchPlayer.objects.select_related('match'),
                to_attr='matches'
            ))
        return qs

    def get_context_data(self, **kwargs):
        context = super(PlayersSuccessful, self).get_context_data(**kwargs)
        players = context['player_list']

        players = players.annotate(
            match_count=Count('matchplayer'),
            wins=Count(Case(
                When(
                    matchplayer__team=F('matchplayer__match__winner'), then=1)
                )
            ),
            losses=F('match_count') - F('wins'),
        )
		
        if not players:
            # no games this season yet, nothing to calc
            return context

        max_vals = players.aggregate(Max('wins'), Max('losses'), Max('score'), Max('ladder_mmr'))
        wins_max = max_vals['wins__max']
        losses_max = max_vals['losses__max']
        score_max = max_vals['score__max']
        ladder_mmr_max = max_vals['ladder_mmr__max']

        matches_max = max(player.match_count for player in players)

        for player in players:
            player.wins_percent = float(player.wins) / wins_max * 100
            player.loss_percent = float(player.losses) / losses_max * 100
            player.score_percent = float(player.score) / score_max * 100
            player.ladder_mmr_percent = float(player.ladder_mmr) / ladder_mmr_max * 100
            player.matches_percent = float(player.match_count) / matches_max * 100

        context.update({
            'player_list': players,
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

        season = LadderSettings.get_solo().current_season
        player.matches = player.matchplayer_set\
            .filter(match__season=season)\
            .select_related('match')

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

        player.matches = player.matches.select_related(
            'scorechange'
        ).prefetch_related(
            Prefetch('match__matchplayer_set',
                     queryset=MatchPlayer.objects.select_related('player'))
        )

    def score_history(self):
        player = self.object

        season = LadderSettings.get_solo().current_season
        score_changes = player.scorechange_set.filter(season=season)\
            .select_related(
                'match', 'match__match',
                'match__match__balance', 'match__match__balance__result'
            )

        max_vals = Player.objects\
            .filter(matchplayer__match__season=season).distinct()\
            .aggregate(Max('score'), Max('ladder_mmr'))
        score_max = max(1, max_vals['score__max'] or player.score)
        mmr_max = max(1, max_vals['ladder_mmr__max'] or player.score)

        score = mmr = 0
        for scoreChange in reversed(score_changes):
            score += scoreChange.score_change
            mmr += scoreChange.mmr_change

            scoreChange.score = score
            scoreChange.mmr = mmr
            scoreChange.score_percent = float(score) / score_max * 100
            scoreChange.mmr_percent = float(mmr) / mmr_max * 100

        return score_changes

    def teammates_stats(self, matches_min=3, opponents=False):
        player = self.object

        matches = player.matchplayer_set\
            .select_related('match', 'scorechange')\
            .prefetch_related(Prefetch(
                'match__matchplayer_set',
                queryset=MatchPlayer.objects.select_related('player')
            ))

        # gather initial teammate stats
        teammates = defaultdict(lambda: defaultdict(int))
        for matchPlayer in matches:
            match = matchPlayer.match
            changes = matchPlayer.scorechange
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
        matches_max = 0
        if teammates:
            matches_max = max(t['match_count'] for t in teammates.values())

        for name, teammate in teammates.items():
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


class MatchList(ListView):
    model = Match
    ordering = ['-date']

    def get_queryset(self):
        qs = super(MatchList, self).get_queryset()

        season = LadderSettings.get_solo().current_season
        qs = qs.filter(matchplayer__match__season=season).distinct()\
            .prefetch_related(Prefetch(
                'matchplayer_set',
                queryset=MatchPlayer.objects.select_related('match')
            ))
        return qs

    def get_context_data(self, **kwargs):
        context = super(MatchList, self).get_context_data(**kwargs)
        matches = context['match_list']

        page_num = self.request.GET.get('page', 1)
        page = Paginator(matches, 50, request=self.request).page(page_num)
        matches = page.object_list

        for match in matches:
            match.radiant = [mp for mp in match.matchplayer_set.all() if mp.team == 0]
            match.dire = [mp for mp in match.matchplayer_set.all() if mp.team == 1]

        context.update({
            'match_list': matches,
            'pagination': page,
        })

        return context


class LadderStats(TemplateView):
    template_name = 'ladder/stats.html'

    def get_context_data(self, **kwargs):
        context = super(LadderStats, self).get_context_data(**kwargs)

        all_time = Match.objects.all()
        this_season = Match.objects.filter(season=LadderSettings.get_solo().current_season)
        last_days = Match.objects.filter(date__gte=datetime.now() - timedelta(days=3))

        context.update({
            'all_time': self.get_stats(all_time),
            'this_season': self.get_stats(this_season),
            'last_days': self.get_stats(last_days),
        })
        return context

    @staticmethod
    def get_stats(matches):
        return matches.aggregate(
            matches=Count('id', distinct=True),
            players=Count('matchplayer__player', distinct=True),
            mmr=Avg('matchplayer__player__dota_mmr'),
        )


class LobbyStatus(TemplateView):
    template_name = 'ladder/lobby_status.html'

    def get_context_data(self, **kwargs):
        context = super(LobbyStatus, self).get_context_data(**kwargs)

        # get current lobbies from cache
        bots = cache.get('bots')
        lobbies = []
        if bots:
            lobbies = [cache.get(bot) for bot in bots]

        # list of all members in all lobbies
        members = itertools.chain(*[lobby['members'] for lobby in lobbies])
        members = Player.objects.filter(dota_id__in=members)

        # get players info from db
        for lobby in lobbies:
            players_num = total_mmr = 0
            for team in lobby['teams']:
                for slot, player in enumerate(team):
                    print('slot: %s   player: %s' % (slot, player))
                    try:
                        player = next(p for p in members if p.dota_id == str(player['dota_id']))
                        team[slot] = player
                        players_num += 1
                        total_mmr += player.dota_mmr
                    except (TypeError, StopIteration):
                        # empty slot or unregistered player, it's fine
                        pass
            lobby['free_slots'] = 10 - players_num
            if players_num > 0:
                lobby['average_mmr'] = total_mmr // players_num

        # calc duration for started games
        for lobby in lobbies:
            if lobby['state'] == 'game':
                lobby['game_duration_mins'] = (datetime.now() - lobby['game_start_time']).seconds // 60

        context.update({
            'lobbies': lobbies,
            'lobbies_ready': sum(lobby['state'] == 'ready' for lobby in lobbies),
            'lobbies_game': sum(lobby['state'] == 'game' for lobby in lobbies),
        })
        return context