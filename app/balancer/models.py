
import collections
from django.db import models
from jsonfield import JSONField


# BalanceResult is a list of all ways to build 2 teams
# with the same 10 players
class BalanceResult(models.Model):
    mmr_exponent = models.FloatField(default=3)


# BalanceAsnwer is a single way to make 2 teams out of 10 players
class BalanceAnswer(models.Model):
    # TODO: check if we need load_kwargs here
    teams = JSONField(load_kwargs={'object_pairs_hook': collections.OrderedDict})
    mmr_diff = models.IntegerField()
    mmr_diff_exp = models.IntegerField()
    result = models.ForeignKey(BalanceResult, related_name='answers', null=True,
                               on_delete=models.CASCADE)
