from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.db.models import Sum

from app.ladder.models import ScoreChange, Match, Player


@receiver([post_save, post_delete], sender=ScoreChange)
def score_change(instance, **kwargs):
    player = instance.player

    vals = player.scorechange_set.aggregate(
        Sum('mmr_change'), Sum('score_change'))

    player.ladder_mmr = vals['mmr_change__sum']
    player.score = vals['score_change__sum']

    player.save()


@receiver(post_delete, sender=Match)
def match_change(**kwargs):
    Player.objects.update_ranks()
