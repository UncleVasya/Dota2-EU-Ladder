from collections import defaultdict
from django.db import models, transaction


class PlayerManager(models.Manager):
    # gives player initial score and mmr
    @staticmethod
    def init_score(player):
        from app.ladder.models import ScoreChange

        avg_mmr = 4000
        initial_mmr = 200 - 30 * (avg_mmr - player.mmr) / 1000

        score = ScoreChange.objects.create(
            player=player,
            score_amount=25,
            mmr_change=initial_mmr,
            info='Season started',
        )
        player.scorechange_set.add(score)

    def update_ranks(self):
        # recalculate player rankings by particular field (ladder_mmr or score)
        def update_ranks_by(field):
            groups = defaultdict(list)
            for player in players:
                value = getattr(player, field)
                groups[value].append(player)

            groups = sorted(groups.items(), reverse=True)
            print groups

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


class MatchManager(models.Manager):
    @staticmethod
    def add_scores(match):
        from app.ladder.models import ScoreChange

        mmr_diff = match.balance.teams[0]['mmr'] - match.balance.teams[1]['mmr']
        underdog = 0 if mmr_diff <= 0 else 1
        underdog_bonus = abs(mmr_diff) / 2

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

        return match


class ScoreChangeManager(models.Manager):
    pass
