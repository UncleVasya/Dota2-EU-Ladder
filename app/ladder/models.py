from __future__ import unicode_literals

from django.db import models
from app.balancer.models import BalanceAnswer
from autoslug import AutoSlugField
from app.ladder.managers import PlayerManager, ScoreChangeManager


class Player(models.Model):
    name = models.CharField(max_length=200, unique=True)
    dota_mmr = models.PositiveIntegerField()
    dota_id = models.CharField(max_length=200, null=True, blank=True)
    slug = AutoSlugField(populate_from='name')

    ladder_mmr = models.PositiveIntegerField(default=0)
    score = models.PositiveIntegerField(default=0)
    rank_ladder_mmr = models.PositiveIntegerField(default=0)
    rank_score = models.PositiveIntegerField(default=0)

    voice_issues = models.BooleanField(default=False)
    bot_access = models.BooleanField(default=False)
    banned = models.BooleanField(default=False)
    blacklist = models.ManyToManyField('self', symmetrical=False, related_name='blacklisted_by')

    objects = PlayerManager()

    class Meta:
        ordering = ['rank_ladder_mmr']

    def __unicode__(self):
        return u'%s' % self.name

    def save(self, *args, **kwargs):
        # TODO: Move this to clean_fields() later
        # TODO: (can't do it atm, because of empty dota_id in test data).
        # TODO: Or even better move this to manager.update_scores()
        # TODO  (this will allow us bulk_update in future)
        self.score = max(self.score, 0)
        self.ladder_mmr = max(self.ladder_mmr, 0)

        created = not self.pk
        super(Player, self).save(*args, **kwargs)

        # give player initial score and mmr
        if created:
            PlayerManager.init_score(self)


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


class ScoreChange(models.Model):
    player = models.ForeignKey(Player)
    score_change = models.SmallIntegerField(default=0)
    mmr_change = models.SmallIntegerField(default=0)
    match = models.OneToOneField(MatchPlayer, null=True, blank=True)
    info = models.CharField(max_length=255)
    date = models.DateTimeField(auto_now_add=True)

    objects = ScoreChangeManager()

    class Meta:
        unique_together = ('player', 'match')
        ordering = ('-id', )
