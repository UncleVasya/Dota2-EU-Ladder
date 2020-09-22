from django.db import models, transaction
from app.balancer.balancer import balance_teams, balance_from_teams, role_balance_teams
from app.ladder.models import LadderSettings


class BalanceResultManager(models.Manager):
    @staticmethod
    def balance_teams(players, role_balancing=True):
        from app.balancer.models import BalanceResult, BalanceAnswer

        # balance teams and save result
        # TODO: make mmr_exponent changable from admin panel
        mmr_exponent = LadderSettings.get_solo().balance_exponent
        if role_balancing:
            answers = role_balance_teams(players, mmr_exponent)
        else:
            players = [(p.name, p.ladder_mmr) for p in players]
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


class BalanceAnswerManager(models.Manager):
    @staticmethod
    def balance_custom(teams):
        from app.balancer.models import BalanceAnswer

        mmr_exponent = LadderSettings.get_solo().balance_exponent
        answer = balance_from_teams(teams, mmr_exponent)

        answer = BalanceAnswer.objects.create(
            teams=answer['teams'],
            mmr_diff=answer['mmr_diff'],
            mmr_diff_exp=answer['mmr_diff_exp'],
        )

        return answer