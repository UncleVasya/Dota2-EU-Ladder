from collections import defaultdict
from django.db import models, transaction


class PlayerManager(models.Manager):
    # gives player initial score and mmr
    @staticmethod
    def init_score(player, reset_mmr=False):
        from app.ladder.models import ScoreChange
        from app.ladder.models import LadderSettings

        if reset_mmr:
            initial_mmr = PlayerManager.dota_to_ladder_mmr(player.dota_mmr)
        else:
            # take mmr from last season
            initial_mmr = player.ladder_mmr

        ScoreChange.objects.create(
            player=player,
            score_change=25,
            mmr_change=initial_mmr,
            info='Season started',
            season=LadderSettings.get_solo().current_season,
        )

        player.min_allowed_mmr = initial_mmr - 1000
        player.max_allowed_mmr = initial_mmr + 1000
        player.save()

    def update_ranks(self):
        from app.ladder.models import LadderSettings

        # recalculate player rankings by particular field (ladder_mmr or score)
        def update_ranks_by(field):
            groups = defaultdict(list)
            for player in players:
                value = getattr(player, field)
                groups[value].append(player)

            groups = sorted(groups.items(), reverse=True)

            rank = 0
            for group in groups:
                rank += len(group[1])
                for player in group[1]:
                    setattr(player, 'rank_%s' % field, rank)
                    player.save()

        season = LadderSettings.get_solo().current_season
        players = self.filter(matchplayer__match__season=season).distinct()
        players = players or self.all()

        update_ranks_by('ladder_mmr')
        update_ranks_by('score')

    @staticmethod
    def dota_to_ladder_mmr(mmr):
        return mmr  # at this moment we don't use any custom formula for mmr

    @staticmethod
    def ladder_to_dota_mmr(mmr):
        return mmr  # at this moment we don't use any custom formula for mmr


class MatchManager(models.Manager):
    @staticmethod
    def add_scores(match):
        from app.ladder.models import ScoreChange
        from app.ladder.models import LadderSettings

        # TODO: make values like win/loss change and underdog bonus changeble in admin panel
        mmr_diff = match.balance.teams[0]['mmr'] - match.balance.teams[1]['mmr']
        underdog = 0 if mmr_diff <= 0 else 1
        underdog_bonus = abs(mmr_diff) // 300 * 15  # 15 mmr points for each 300 avg. mmr diff
        underdog_bonus = min(15, underdog_bonus)  # but no more than 15 mmr

        print('mmr diff: %d' % mmr_diff)
        print('underdog: %d' % underdog)
        print('underdog bonus: %d' % underdog_bonus)
        print('')

        for matchPlayer in match.matchplayer_set.all():
            is_victory = 1 if matchPlayer.team == match.winner else -1
            is_underdog = 1 if matchPlayer.team == underdog else -1

            score_change = 1 * is_victory

            mmr_per_game = LadderSettings.get_solo().mmr_per_game
            mmr_change = mmr_per_game * is_victory
            mmr_change += underdog_bonus * is_underdog

            use_boundary = False  # TODO: get this values from LadderSettings
            if use_boundary:
                # make sure new ladder mmr is in boundaries
                player = matchPlayer.player
                new_mmr = player.ladder_mmr + mmr_change
                new_mmr = max(player.min_allowed_mmr, min(new_mmr, player.max_allowed_mmr))
                mmr_change = new_mmr - player.ladder_mmr

            ScoreChange.objects.create(
                player=matchPlayer.player,
                score_change=score_change,
                mmr_change=mmr_change,
                match=matchPlayer,
                season=LadderSettings.get_solo().current_season,
            )

    @staticmethod
    def record_balance(answer, winner, dota_id=None):
        from app.ladder.models import Player, Match, MatchPlayer
        from app.ladder.models import LadderSettings

        players = [p[0] for t in answer.teams for p in t['players']]
        players = Player.objects.filter(name__in=players)

        # check that all players from balance exist
        # (we don't allow CustomBalance results here)
        if len(players) < 10:
            return None

        with transaction.atomic():
            match = Match.objects.create(
                winner=winner,
                balance=answer,
                season=LadderSettings.get_solo().current_season,
                dota_id=dota_id,
            )

            for i, team in enumerate(answer.teams):
                for player in team['players']:
                    player = next(p for p in players if p.name == player[0])

                    MatchPlayer.objects.create(
                        match=match,
                        player=player,
                        team=i
                    )

            MatchManager.add_scores(match)
            Player.objects.update_ranks()

        return match


class ScoreChangeManager(models.Manager):
    pass
