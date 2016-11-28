from __future__ import unicode_literals
from collections import defaultdict, OrderedDict

from django.db import models


class Player(models.Model):
    name = models.CharField(max_length=200, unique=True)
    rank = models.PositiveIntegerField(default=0)
    score = models.PositiveIntegerField(default=25)
    mmr = models.PositiveIntegerField()
    dota_id = models.CharField(max_length=200)

    class Meta:
        ordering = ['rank']

    def save(self, calc_ranks=True, *args, **kwargs):
        super(Player, self).save(*args, **kwargs)

        if not calc_ranks:
            return

        # recalculate player rankings based on score
        score_groups = defaultdict(list)
        for player in Player.objects.all():
            score_groups[player.score].append(player)

        score_groups = sorted(score_groups.items(), reverse=True)
        print score_groups

        for rank, group in enumerate(score_groups):
            for player in group[1]:
                player.rank = rank + 1
                player.save(calc_ranks=False)
