NEGATIVE_REASONS = ['toxic', 'afk', 'grief', 'dc', 'cheating']
POSITIVE_REASONS = ['helpful', 'friendly', 'good leadership', 'strategic thinking', 'team player']

INVALID_FORMAT_MESSAGE = ("Invalid command format. Please specify:\n"
                          "Player Reason Match ID(skip if reporting last game) and a Comment(max 255 chars) \n"
                          "Example: \n"
                          "`!report SATO afk This guy is really annoying, beware of play with him!!!`")
PLAYER_NOT_FOUND_MESSAGE = "Could not find one or both players."
MATCH_NOT_FOUND_MESSAGE = "Could not find the specified match."
REPORT_SUCCESS_MESSAGE = "{} has reported {} for {} with this comment: \n{}"
TIP_SUCCESS_MESSAGE = "{} has tipped {} for {} with this comment: \n{}"
TIP_NO_MATCH_TO_REPORT = "No recent match found for {}."
NOT_PLAYED_TOGETHER = "The reported and reporter players did not play together in the specified match."
MATCH_TOO_OLD = "The match was more than 2 days ago and cannot be reported/tipped."
PLAYER_WITHOUT_GAMES = "No recent match found for the reported player."
NOT_VALID_REASON = "Not a valid reason. Valid reasons are {}"

from django.utils.timezone import now
from django.core.management.base import BaseCommand
from app.ladder.models import Player, Match, PlayerReport, MatchPlayer  # Adjust the import path as necessary


class ReportTipCommands(BaseCommand):
    def __init__(self, name):
        super().__init__()
        self.name = name

    async def process_command(self, msg, is_tip=False):
        discord_id = msg.author.id
        command = msg.content
        parts = command.split()

        if len(parts) < 3:
            await msg.channel.send(INVALID_FORMAT_MESSAGE)
            return

        reported_name = parts[1]
        reason = parts[2]
        match_id = parts[3] if len(parts) > 3 and parts[3].isdigit() else None
        comment = " ".join(parts[4:]) if match_id else " ".join(parts[3:])

        valid_reasons = POSITIVE_REASONS if is_tip else NEGATIVE_REASONS
        if reason not in valid_reasons:
            await msg.channel.send(NOT_VALID_REASON.format(', '.join(valid_reasons)))
            return

        try:
            reporter = Player.objects.get(
                discord_id=str(discord_id))  # Ensure discord_id is properly cast to string if needed
            reported = Player.objects.get(name=reported_name)

            if match_id:
                match = Match.objects.get(id=match_id)
            else:
                # Fetch the last match ID for the reported player and use it
                match_id = reported.get_last_match_id()
                match = Match.objects.get(id=match_id) if match_id else None

            if not match:
                await msg.channel.send(PLAYER_WITHOUT_GAMES)
                return

            # Check if the match was within the last 2 days
            if (now() - match.date).days > 2:
                await msg.channel.send(MATCH_TOO_OLD)
                return

            # Verify both players participated in the match
            if not MatchPlayer.objects.filter(match=match, player=reporter).exists() or not MatchPlayer.objects.filter(
                    match=match, player=reported).exists():
                await msg.channel.send(NOT_PLAYED_TOGETHER)
                return

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

            success_message = TIP_SUCCESS_MESSAGE if is_tip else REPORT_SUCCESS_MESSAGE
            await msg.channel.send(success_message.format(reporter.name, reported_name, reason, comment))

        except Player.DoesNotExist:
            await msg.channel.send(PLAYER_NOT_FOUND_MESSAGE)
            return
        except Match.DoesNotExist:
            await msg.channel.send(MATCH_NOT_FOUND_MESSAGE)
            return

    async def report_player_command(self, msg):
        await self.process_command(msg, is_tip=False)

    async def tip_player_command(self, msg):
        await self.process_command(msg, is_tip=True)
