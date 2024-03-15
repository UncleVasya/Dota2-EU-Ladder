NEGATIVE_REASONS = ['toxic', 'afk', 'grief', 'dc', 'cheating', 'drunk']
POSITIVE_REASONS = ['helpful', 'friendly', 'leader', 'mindgames', 'teamplayer']

PLAYER_NOT_FOUND_MESSAGE = "Could not find one or both players."
MATCH_NOT_FOUND_MESSAGE = "Could not find the specified match."
TIP_NO_MATCH_TO_REPORT = "No recent match found for {}."
NOT_PLAYED_TOGETHER = "The reported and reporter players did not play together in the specified match {}."
MATCH_TOO_OLD = "The match was more than 2 days ago and cannot be reported/tipped."
PLAYER_WITHOUT_GAMES = "No recent match found for the reported player."
NOT_VALID_REASON = "Not a valid reason. Valid reasons are {}"
DUPLICATE_REPORT = "A report for this match and player combination already exists. Duplicate reports are not allowed."

from django.utils.timezone import now
from django.db.utils import IntegrityError
from app.ladder.models import Player, Match, PlayerReport, MatchPlayer  # Adjust the import path as necessary
from typing import Union

class ReportTipCommands:
    def process_command(self, reporter: Player, reported: Player, reason: str, match_id: str = None, comment: str = '', is_tip: bool = False) -> Union[PlayerReport, str]:
        valid_reasons = POSITIVE_REASONS if is_tip else NEGATIVE_REASONS
        if reason not in valid_reasons:
            return NOT_VALID_REASON.format(', '.join(valid_reasons))

        try:
            if match_id:
                match = Match.objects.get(dota_id=match_id)
            else:
                match_id = reporter.get_last_match_dota_id()
                if not match_id:
                    return TIP_NO_MATCH_TO_REPORT.format(reported.name)

                match = Match.objects.get(dota_id=match_id)

            if (now() - match.date).days > 2:
                return MATCH_TOO_OLD

            if not MatchPlayer.objects.filter(match=match, player=reporter).exists() or not MatchPlayer.objects.filter(match=match, player=reported).exists():
                return NOT_PLAYED_TOGETHER.format(match.dota_id)

            report_value = 1 if is_tip else -1
            report = PlayerReport(
                from_player=reporter,
                to_player=reported,
                match=match,
                reason=reason,
                comment=comment,
                value=report_value
            )
            report.save()
            return report
            # success_message = TIP_SUCCESS_MESSAGE if is_tip else REPORT_SUCCESS_MESSAGE
            # return success_message.format(reporter.name, reported.name, reason, match.id, comment)

        except Match.DoesNotExist:
            return MATCH_NOT_FOUND_MESSAGE
        except IntegrityError:
            return DUPLICATE_REPORT

    def report_player_command(self, reporter: Player, reported: Player, reason: str, match_id: str = None, comment: str = '') -> Union[PlayerReport, str]:
        return self.process_command(reporter, reported, reason, match_id, comment, is_tip=False)

    def tip_player_command(self, reporter: Player, reported: Player, reason: str, match_id: str = None, comment: str = '') -> Union[PlayerReport, str]:
        return self.process_command(reporter, reported, reason, match_id, comment, is_tip=True)