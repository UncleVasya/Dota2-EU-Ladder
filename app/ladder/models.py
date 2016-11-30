from __future__ import unicode_literals
from collections import defaultdict

from django.db import models


class Player(models.Model):
    name = models.CharField(max_length=200, unique=True)
    rank = models.PositiveIntegerField(default=0)
    score = models.PositiveIntegerField(default=25)
    mmr = models.PositiveIntegerField()
    dota_id = models.CharField(max_length=200)

    class Meta:
        ordering = ['rank']

    def __unicode__(self):
        return u'%s' % self.name

    # TODO: make model manager and move it there?
    def save(self, calc_ranks=True, *args, **kwargs):
        super(Player, self).save(*args, **kwargs)

        if not calc_ranks:
            return

        # recalculate player rankings based on score
        score_groups = defaultdict(list)
        for player in Player.objects.all():
            score_groups[player.score].append(player)

        score_groups = sorted(score_groups.items(), reverse=True)

        for rank, group in enumerate(score_groups):
            for player in group[1]:
                player.rank = rank + 1
                player.save(calc_ranks=False)


class Match(models.Model):
    players = models.ManyToManyField(Player, through='MatchPlayer')
    winner = models.PositiveSmallIntegerField()
    date = models.DateTimeField(auto_now_add=True)


class MatchPlayer(models.Model):
    match = models.ForeignKey(Match)
    player = models.ForeignKey(Player)
    team = models.PositiveSmallIntegerField()

    class Meta:
        unique_together = ('player', 'match')

    def save(self, *args, **kwargs):
        super(MatchPlayer, self).save(*args, **kwargs)

        if self.team == self.match.winner:
            self.player.score += 1
        else:
            self.player.score -= 1

        self.player.save()
