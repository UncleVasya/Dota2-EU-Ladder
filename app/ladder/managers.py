from collections import defaultdict
from django.db import models, transaction


class PlayerManager(models.Manager):
    # gives player initial score and mmr
    @staticmethod
    def init_score(player):
        from app.ladder.models import ScoreChange

        initial_mmr = PlayerManager.dota_to_ladder_mmr(player.dota_mmr)
        score = ScoreChange.objects.create(
            player=player,
            score_change=25,
            mmr_change=initial_mmr,
            info='Season started',
        )
        player.scorechange_set.add(score)  # TODO: looks like this line isn't needed

        player.min_allowed_mmr = initial_mmr - 20
        player.max_allowed_mmr = initial_mmr + 20
        player.save()

    def update_ranks(self):
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

        # TODO: make 'active' field Player model
        players = self.exclude(name__in=['hoxieloxie'])

        players = players.filter(matchplayer__isnull=False).distinct()
        players = players or self.all()

        update_ranks_by('ladder_mmr')
        update_ranks_by('score')

    @staticmethod
    def dota_to_ladder_mmr(mmr):
        avg_mmr = 4000
        return 200 - 30 * (avg_mmr - mmr) / 1000

    @staticmethod
    def ladder_to_dota_mmr(mmr):
        avg_mmr = 4000
        return avg_mmr - (200 - mmr) * 1000 / 30


class MatchManager(models.Manager):
    @staticmethod
    def add_scores(match):
        from app.ladder.models import ScoreChange

        # TODO: make values like win/loss change and underdog bonus changeble in admin panel
        mmr_diff = match.balance.teams[0]['mmr'] - match.balance.teams[1]['mmr']
        underdog = 0 if mmr_diff <= 0 else 1
        underdog_bonus = abs(mmr_diff) / 10  # 1 point for each 10 avg. mmr diff
        underdog_bonus = min(1, underdog_bonus)  # but no more than 1

        print 'mmr diff: %d' % mmr_diff
        print 'underdog: %d' % underdog
        print 'underdog bonus: %d / 50 = %d' % (abs(mmr_diff), underdog_bonus)
        print ''

        for matchPlayer in match.matchplayer_set.all():
            is_victory = 1 if matchPlayer.team == match.winner else -1
            is_underdog = 1 if matchPlayer.team == underdog else -1

            score_change = 1 * is_victory

            mmr_change = 7 * is_victory
            mmr_change += underdog_bonus * is_underdog

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
            )

    @staticmethod
    def record_balance(answer, winner):
        from app.ladder.models import Player, Match, MatchPlayer

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
