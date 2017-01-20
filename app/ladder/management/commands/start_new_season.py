from app.ladder.managers import PlayerManager
from app.ladder.models import LadderSettings, Player
from django.db import transaction
from django.core.management import BaseCommand


class Command(BaseCommand):
    def handle(self, *args, **options):
        with transaction.atomic():
            ladder = LadderSettings.get_solo()
            ladder.current_season += 1
            ladder.save()

            for player in Player.objects.all():
                PlayerManager.init_score(player)
