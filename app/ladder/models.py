from __future__ import unicode_literals

from django.db import models
from dota2_eu_ladder.managers import PlayerManager


class Player(models.Model):
    name = models.CharField(max_length=200, unique=True)
    rank = models.PositiveIntegerField(default=0)
    score = models.PositiveIntegerField(default=25)
    mmr = models.PositiveIntegerField()
    dota_id = models.CharField(max_length=200)

    objects = PlayerManager()

    class Meta:
        ordering = ['rank']

    def __unicode__(self):
        return u'%s' % self.name

    def save(self, *args, **kwargs):
        # TODO: move this to clean_fields() later
        # TODO: (can't do it atm, because of empty dota_id in test data)
        self.score = max(self.score, 0)

        super(Player, self).save(*args, **kwargs)


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

        # TODO: update scores in one query on match add
        if self.team == self.match.winner:
            self.player.score += 1
        else:
            self.player.score -= 1

        self.player.save()
