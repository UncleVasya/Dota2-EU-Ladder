from __future__ import unicode_literals

from django.db import models
from django.db.models import Sum
from app.balancer.models import BalanceAnswer
from autoslug import AutoSlugField
from dota2_eu_ladder.managers import PlayerManager, ScoreChangeManager


class Player(models.Model):
    name = models.CharField(max_length=200, unique=True)
    rank = models.PositiveIntegerField(default=0)
    score = models.PositiveIntegerField(default=0)
    mmr = models.PositiveIntegerField()
    ladder_mmr = models.PositiveIntegerField(default=0)
    dota_id = models.CharField(max_length=200)
    slug = AutoSlugField(populate_from='name')

    objects = PlayerManager()

    class Meta:
        ordering = ['rank']

    def __unicode__(self):
        return u'%s' % self.name

    def save(self, *args, **kwargs):
        # TODO: Move this to clean_fields() later
        # TODO: (can't do it atm, because of empty dota_id in test data).
        # TODO: Or even better move this to manager.update_scores()
        # TODO  (this will allow us bulk_update in future)
        self.score = max(self.score, 0)
        self.ladder_mmr = max(self.ladder_mmr, 0)

        super(Player, self).save(*args, **kwargs)


class Match(models.Model):
    players = models.ManyToManyField(Player, through='MatchPlayer')
    winner = models.PositiveSmallIntegerField()
    balance = models.OneToOneField(BalanceAnswer, null=True)
    date = models.DateTimeField(auto_now_add=True)


class MatchPlayer(models.Model):
    match = models.ForeignKey(Match)
    player = models.ForeignKey(Player)
    team = models.PositiveSmallIntegerField()

    class Meta:
        unique_together = ('player', 'match')
        # TODO: replace '-match__date' with '-id' and check that:
        # TODO: - nothing breaks
        # TODO: - speed increased
        ordering = ('-match__date', 'team')

    def save(self, *args, **kwargs):
        super(MatchPlayer, self).save(*args, **kwargs)

        # # TODO: update scores in one query on match add
        # if self.team == self.match.winner:
        #     self.player.score += 1
        # else:
        #     self.player.score -= 1
        #
        # victory = 1 if self.team == self.match.winner else -1
        #
        # score_change = 1 * victory
        # mmr_change = 15 * victory
        #
        # ScoreChange.objects.create(
        #     player=self.player,
        #     amount=score_change,
        #     mmr_change=mmr_change,
        #     match=self,
        # )
        #
        # self.player.save()


class ScoreChange(models.Model):
    player = models.ForeignKey(Player)
    amount = models.SmallIntegerField(default=0)
    mmr_change = models.SmallIntegerField(default=0)
    match = models.ForeignKey(MatchPlayer, null=True, blank=True)
    info = models.CharField(max_length=255)
    date = models.DateTimeField(auto_now_add=True)

    objects = ScoreChangeManager()

    class Meta:
        unique_together = ('player', 'match')
        ordering = ('-id', )

    def save(self, *args, **kwargs):
        super(ScoreChange, self).save()

        self.player.score = self.player.scorechange_set.aggregate(
            Sum('amount')
        )['amount__sum']

        self.player.ladder_mmr = self.player.scorechange_set.aggregate(
            Sum('mmr_change')
        )['mmr_change__sum']

        self.player.save()
