from django.db import models, transaction
from app.balancer.balancer import balance_teams


class BalanceResultManager(models.Manager):
    @staticmethod
    def balance_teams(players):
        from app.balancer.models import BalanceResult, BalanceAnswer

        # balance teams and save result
        mmr_exponent = 3
        answers = balance_teams(players, mmr_exponent)

        with transaction.atomic():
            result = BalanceResult.objects.create(mmr_exponent=mmr_exponent)
            for answer in answers:
                BalanceAnswer.objects.create(
                    teams=answer['teams'],
                    mmr_diff=answer['mmr_diff'],
                    mmr_diff_exp=answer['mmr_diff_exp'],
                    result=result
                )

        return result