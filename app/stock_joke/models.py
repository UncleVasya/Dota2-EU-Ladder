from django.db import models
from solo.models import SingletonModel


class StockBuyer(models.Model):
    name = models.CharField(max_length=200, unique=True)
    discord_id = models.PositiveIntegerField(unique=True)
    entry_price = models.FloatField()


class StockJokeSettings(SingletonModel):
    enabled = models.BooleanField(default=False)
    stock_ticker = models.CharField(max_length=200, default='GME')
    discord_server_id = models.PositiveIntegerField(null=True, blank=True)
    greed_role_id = models.PositiveIntegerField(null=True, blank=True)
    red_role_id = models.PositiveIntegerField(null=True, blank=True)
