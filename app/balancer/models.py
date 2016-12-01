from __future__ import unicode_literals
import collections
from django.db import models
from app.ladder.models import Match
from jsonfield import JSONField


# BalanceAsnwer is a single way to make 2 teams out of 10 players
class BalanceAnswer(models.Model):
    # TODO: check if we need load_kwargs here
    answer = JSONField(load_kwargs={'object_pairs_hook': collections.OrderedDict})
    result = models.ForeignKey('BalanceResult', related_name='answers')
    match = models.ForeignKey(Match, null=True)  # if a match was played with these teams


# BalanceResult is a list of all ways to build 2 teams
# with the same 10 players
class BalanceResult(models.Model):
    pass
