from app.ladder.managers import PlayerManager
from app.ladder.models import LadderSettings, Player, ScoreChange, RolesPreference
from django.db import transaction
from django.core.management import BaseCommand


class Command(BaseCommand):
    def handle(self, *args, **options):
        with transaction.atomic():
            for p in Player.objects.all():
                try:
                    roles = p.roles
                except Player.roles.RelatedObjectDoesNotExist:
                    p.roles = RolesPreference.objects.create()
                    p.save()
                    print(f'Roles fixed for: {p.name}')

            for role in RolesPreference.objects.all():
                try:
                    print(role.player)
                except:
                    role.delete()
