from app.ladder.managers import PlayerManager
from app.ladder.models import Player, ScoreChange
from django.core.management import BaseCommand


class Command(BaseCommand):
    def handle(self, *args, **options):
        for player in Player.objects.all():
            initial_mmr = PlayerManager.dota_to_ladder_mmr(player.dota_mmr)

            print 'Player: %s   Dota MMR: %s   Ladder MMR: %s' % \
                  (player, player.dota_mmr, initial_mmr)

            player.min_allowed_mmr = initial_mmr - 20
            player.max_allowed_mmr = initial_mmr + 20
            player.save()

            print 'min: %s   max: %s' % \
                  (player.min_allowed_mmr, player.max_allowed_mmr)
            print

            if player.ladder_mmr < player.min_allowed_mmr:
                ScoreChange.objects.create(
                    player=player,
                    score_change=0,
                    mmr_change=player.min_allowed_mmr - player.ladder_mmr,
                    info='Introducing MMR boundaries',
                )
            elif player.ladder_mmr > player.max_allowed_mmr:
                ScoreChange.objects.create(
                    player=player,
                    score_change=0,
                    mmr_change=player.max_allowed_mmr - player.ladder_mmr,
                    info='Introducing MMR boundaries',
                )