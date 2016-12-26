from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.db.models import Sum

from app.ladder.models import ScoreChange, Match, Player


@receiver([post_save, post_delete], sender=ScoreChange)
def score_change(sender, instance, **kwargs):
    print '\n'
    print 'score_change signal'
    print 'sender: %s  instance: %s  kwargs: %s' % (sender, instance, kwargs)

    player = instance.player

    player.score = player.scorechange_set.aggregate(
        Sum('score_change')
    )['score_change__sum']

    player.ladder_mmr = player.scorechange_set.aggregate(
        Sum('mmr_change')
        )['mmr_change__sum']

    player.save()

    print 'Player mmr, score: %s, %s' % (player.mmr, player.score)


@receiver([post_save, post_delete], sender=Match)
def match_change(sender, instance, **kwargs):
    print '\n'
    print 'match_change signal'
    print 'sender: %s  instance: %s  kwargs: %s' % (sender, instance, kwargs)

    print 'Updating ranks'
    Player.objects.update_ranks()
