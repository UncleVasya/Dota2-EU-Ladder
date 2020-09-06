from app.ladder.managers import PlayerManager
from app.ladder.models import LadderSettings, Player, ScoreChange
from django.db import transaction
from django.core.management import BaseCommand


class Command(BaseCommand):
    # this is a one-time command to update our MMR system
    def handle(self, *args, **options):
        with transaction.atomic():
            for player in Player.objects.all():
                mmr_diff = player.dota_mmr - player.ladder_mmr
                ScoreChange.objects.create(
                    player=player,
                    mmr_change=mmr_diff,
                    info='Updated MMR system',
                    season=LadderSettings.get_solo().current_season,
                )
