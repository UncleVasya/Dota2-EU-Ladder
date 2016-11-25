from __future__ import unicode_literals
import collections

from django.db import models
from jsonfield import JSONField


class BalanceResult(models.Model):
    answers = JSONField(load_kwargs={'object_pairs_hook': collections.OrderedDict})
