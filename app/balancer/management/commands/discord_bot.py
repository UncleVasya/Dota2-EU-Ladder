from statistics import mean

import discord
from django.core.management.base import BaseCommand
import os

from django.core.urlresolvers import reverse
from django.db.models import Q, Count, Prefetch, Case, When, F

from app.balancer.managers import BalanceResultManager
from app.balancer.models import BalanceAnswer
from app.ladder.managers import PlayerManager, MatchManager
from app.ladder.models import Player, LadderSettings, LadderQueue, QueuePlayer, QueueChannel, MatchPlayer


class Command(BaseCommand):
    def __init__(self):
        super().__init__()
        self.bot = None

    def handle(self, *args, **options):
        bot_token = os.environ.get('DISCORD_BOT_TOKEN', '')

        self.bot = discord.Client()

        @self.bot.event
        async def on_ready():
            print(f'Logged in: {self.bot.user} {self.bot.user.id}')

        @self.bot.event
        async def on_message(msg):
            if not QueueChannel.objects.filter(discord_id=msg.channel.id).exists():
                return

            if msg.author.bot:
                return

            print(f'Got message from {msg.author} ({msg.author.id}): {msg.content}')

            # strip whitespaces so bot can handle strings like " !register   Bob   4000"
            msg.content = " ".join(msg.content.split())
            if msg.content.startswith('!'):
                # looks like this is a bot command
                await self.bot_cmd(msg)

        self.bot.run(bot_token)

    async def bot_cmd(self, msg):
        command = msg.content.split(' ')[0]

        commands = {
            '!register': self.register_command,
            '!vouch': self.vouch_command,
            '!wh': self.whois_command,
            '!whois': self.whois_command,
            '!stats': self.whois_command,
            '!q+': self.join_queue_command,
            '!q-': self.leave_queue_command,
            '!q': self.show_queues_command,
            '!join': self.join_queue_command,
            '!leave': self.leave_queue_command,
            '!list': self.show_queues_command,
            '!add': self.add_to_queue_command,
            '!kick': self.kick_from_queue_command,
            '!mmr': self.mmr_command,
            '!top': self.top_command,
        }
        free_for_all = ['!register']
        staff_only = ['!vouch', '!add', '!kick', '!mmr']

        # if command is free for all, no other checks required
        if command in free_for_all:
            await commands[command](msg)
            return

        # get player from DB using discord id
        try:
            player = Player.objects.get(discord_id=msg.author.id)
        except Player.DoesNotExist:
            await msg.channel.send(f'{msg.author.name}, who the fuck are you?')
            return

        if player.banned:
            await msg.channel.send(f'{msg.author.name}, you are banned.')
            return

        # check permissions when needed
        if not player.bot_access:
            # only staff can use this commands
            if command in staff_only:
                await msg.channel.send(f'{msg.author.name}, this command is staff-only.')
                return

        # user can use this command
        await commands[command](msg, **{'player': player})

    async def register_command(self, msg, **kwargs):
        command = msg.content
        print()
        print('!register command')
        print(command)

        try:
            params = command.split(None, 1)[1]  # get params string
            params = params.rsplit(None, 2)  # split params string into a list

            name = params[0]
            mmr = int(params[1])
            dota_id = str(int(params[2]))  # check that id is a number
        except (IndexError, ValueError):
            await msg.channel.send(
                'Wrong command usage.\n' 
                'Format: "!register username mmr dota_id". Example: \n' 
                '!register Uvs 3000 444510529'
            )
            return

        # check if we can register this player
        if Player.objects.filter(Q(discord_id=msg.author.id) | Q(dota_id=dota_id)).exists():
            await msg.channel.send('Already registered, bro.')
            return

        if Player.objects.filter(name__iexact=name).exists():
            await msg.channel.send(
                'This name is already taken. Try another or talk to admins.'
            )
            return

        # all is good, can register
        Player.objects.create(
            name=name,
            dota_mmr=mmr,
            dota_id=dota_id,
            discord_id=msg.author.id,
        )
        Player.objects.update_ranks()

        admins_to_ping = Player.objects.filter(new_reg_pings=True)
        await msg.channel.send(
            f"""Welcome to the ladder, `{name}`! 
            \nYou need to get vouched before you can play. Wait for inhouse staff to review your signup. 
            \nYou can ping their lazy asses if it takes too long ;)
            \n{' '.join(self.player_mention(p) for p in admins_to_ping)}"""
        )

    async def vouch_command(self, msg, **kwargs):
        command = msg.content
        print()
        print('Vouch command:')
        print(command)

        try:
            name = command.split(None, 1)[1]
        except (IndexError, ValueError):
            return

        player = Command.get_player_by_name(name)
        if not player:
            await msg.channel.send(f'`{name}`: I don\'t know him')
            return

        player.vouched = True
        player.save()

        await msg.channel.send(
            f'{self.player_mention(player)} has been vouched. He can play now!'
        )

    async def whois_command(self, msg, **kwargs):
        command = msg.content
        print()
        print('Whois command:')
        print(command)

        player = name = None
        try:
            name = command.split(None, 1)[1]
        except (IndexError, ValueError):
            #  if name is not provided, show current player
            player = kwargs['player']

        player = player or Command.get_player_by_name(name)
        if not player:
            await msg.channel.send(f'`{name}`: I don\'t know him')
            return

        dotabuff = f'https://www.dotabuff.com/players/{player.dota_id}'

        host = os.environ.get('BASE_URL', 'localhost:8000')
        url = reverse('ladder:player-overview', args=(player.name,))
        player_url = f'{host}{url}'

        season = LadderSettings.get_solo().current_season
        player.matches = player.matchplayer_set \
            .filter(match__season=season) \
            .select_related('match')
        wins = sum(1 if m.match.winner == m.team else 0 for m in player.matches)
        losses = len(player.matches) - wins

        await msg.channel.send(
            f'```\n'
            f'{player.name}\n'
            f'MMR: {player.dota_mmr}\n'
            f'Dotabuff: {dotabuff}\n'
            f'Ladder: {player_url}\n\n'
            f'Ladder MMR: {player.ladder_mmr}\n'
            f'Score: {player.score}\n'
            f'Games: {len(player.matches)} ({wins}-{losses})\n\n'
            f'Vouched: {"yes" if player.vouched else "no"}\n'
            f'```'
        )

    async def join_queue_command(self, msg, **kwargs):
        command = msg.content
        player = kwargs['player']
        print(f'Join command from {player}:\n {command}')

        # check if player is vouched
        if not player.vouched:
            await msg.channel.send('You need to get vouched before you can play.')
            return

        # check if this is a queue channel
        try:
            channel = QueueChannel.objects.get(discord_id=msg.channel.id)
        except QueueChannel.DoesNotExist:
            return

        # check that player has enough MMR
        if player.ladder_mmr < channel.min_mmr:
            await msg.channel.send('Your dick is too small. Grow a bigger one.')
            return

        # check that player is not in a queue already
        if player.ladderqueue_set.filter(active=True):
            await msg.channel.send('Already queued, friend.')
            return

        queue = Command.add_player_to_queue(player, channel)

        await msg.channel.send(
            f'`{player}` joined inhouse queue #{queue.id}.\n' +
            Command.queue_str(queue)
        )

        if queue.players.count() == 10:
            Command.balance_queue(queue)  # todo move this to QueuePlayer signal
            await msg.channel.send(
                '\nQueue is full! Proposed balance: \n' +
                Command.balance_str(queue.balance) + '\n' +
                ' '.join(self.player_mention(p) for p in queue.players.all()) +
                '\nYou have 5 min to join the lobby.'
            )

    async def leave_queue_command(self, msg, **kwargs):
        command = msg.content
        player = kwargs['player']
        print(f'Leave command from {player}:\n {command}')

        deleted, _ = QueuePlayer.objects\
            .filter(player=player, queue__active=True)\
            .delete()

        if deleted > 0:
            await msg.channel.send(f'`{player}` left the queue.\n')
        else:
            await msg.channel.send(f'`{player}` is not queuing.\n')

    async def show_queues_command(self, msg, **kwargs):
        queues = LadderQueue.objects.filter(active=True)
        if queues:
            await msg.channel.send(
                ''.join(Command.queue_str(q) for q in queues)
            )
        else:
            await msg.channel.send('Noone is currently queueing.')

    async def add_to_queue_command(self, msg, **kwargs):
        command = msg.content
        print(f'add_to_queue command from:\n {command}')

        try:
            name = command.split(None, 1)[1]
        except (IndexError, ValueError):
            return

        player = Command.get_player_by_name(name)
        if not player:
            await msg.channel.send(f'`{name}`: I don\'t know him')
            return

        # check that player is not in a queue already
        if player.ladderqueue_set.filter(active=True):
            await msg.channel.send(f'`{player}` is already in a queue')
            return

        # check if this is a queue channel
        try:
            channel = QueueChannel.objects.get(discord_id=msg.channel.id)
        except QueueChannel.DoesNotExist:
            return

        queue = Command.add_player_to_queue(player, channel)

        await msg.channel.send(
            f'By a shameless abuse of power `{msg.author.name}` '
            f'forcefully added {self.player_mention(player)} to the inhouse queue. '
            f'Have fun! ;)'
        )

        if queue.players.count() == 10:
            Command.balance_queue(queue)
            await msg.channel.send(
                '\nQueue is full! Proposed balance: \n' +
                Command.balance_str(queue.balance) + '\n' +
                ' '.join(self.player_mention(p) for p in queue.players.all()) +
                '\nYou have 5 min to join the lobby.'
            )

    async def kick_from_queue_command(self, msg, **kwargs):
        command = msg.content
        print(f'Kick command:\n {command}')

        try:
            name = command.split(None, 1)[1]
        except (IndexError, ValueError):
            return

        player = Command.get_player_by_name(name)
        if not player:
            await msg.channel.send(f'`{name}`: I don\'t know him')
            return

        deleted, _ = QueuePlayer.objects \
            .filter(player=player, queue__active=True) \
            .delete()

        if deleted > 0:
            player_discord = self.bot.get_user(int(player.discord_id))
            mention = player_discord.mention if player_discord else player.name
            await msg.channel.send(f'{mention} was kicked from the queue.')
        else:
            await msg.channel.send(f'`{player}` is not queuing.\n')

    async def mmr_command(self, msg, **kwargs):
        command = msg.content
        print(f'\n!mmr command:\n{command}')

        try:
            min_mmr = int(command.split(' ')[1])
            min_mmr = max(0, min(9000, min_mmr))
        except (IndexError, ValueError):
            return

        try:
            channel = QueueChannel.objects.get(discord_id=msg.channel.id)
        except QueueChannel.DoesNotExist:
            return

        if LadderQueue.objects.filter(channel=channel, active=True).exists():
            await msg.channel.send(
                f'Cannot change MMR when there are active queue in the channel')
            return

        channel.min_mmr = min_mmr
        channel.save()

        await msg.channel.send(f'Min MMR set to {min_mmr}')

    async def top_command(self, msg, **kwargs):
        def get_top_players(limit):
            season = LadderSettings.get_solo().current_season
            qs = Player.objects \
                .order_by('-score', '-ladder_mmr') \
                .filter(matchplayer__match__season=season).distinct() \
                .prefetch_related(Prefetch(
                    'matchplayer_set',
                    queryset=MatchPlayer.objects.select_related('match'),
                    to_attr='matches'
                ))

            qs = qs.annotate(
                match_count=Count('matchplayer'),
                wins=Count(Case(
                    When(
                        matchplayer__team=F('matchplayer__match__winner'), then=1)
                )
                ),
                losses=F('match_count') - F('wins'),
            )

            return qs[:limit]

        def player_str(p):
            # pretty format is tricky
            # TODO: let's move to discord embeds asap
            name_offset = 25 - len(p.name)
            result = f'{p.name}: {" " * name_offset} {p.score}  ' \
                     f'{p.wins}W-{p.losses}L  {p.ladder_mmr} ihMMR'

            return result

        command = msg.content
        print(f'\n!top command:\n{command}')

        try:
            limit = int(command.split(' ')[1])
        except IndexError:
            limit = 10  # default value
        except ValueError:
            return

        host = os.environ.get('BASE_URL', 'localhost:8000')
        url = f'{host}{reverse("ladder:player-list-score")}'

        if limit < 1:
            await msg.channel.send('Haha, very funny :thinking:')
            return

        if limit > 15:
            await msg.channel.send(f'Just open the leaderboard: {url}')
            return

        # all is ok, can show top players
        players = get_top_players(limit)
        top_str = '\n'.join(
            f'{p.rank_score:2}. {player_str(p)}' for p in players
        )
        await msg.channel.send(
            f'```{top_str} ``` \n'
            f'Full leaderboard is here: {url}'
        )


    @staticmethod
    def add_player_to_queue(player, channel):
        # get an available active queue
        queue = LadderQueue.objects \
            .filter(active=True) \
            .annotate(Count('players')) \
            .filter(players__count__lt=10, channel=channel) \
            .first()

        if not queue:
            queue = LadderQueue.objects.create(
                min_mmr=channel.min_mmr,  # todo this should be done automatically when saving a new queue instance
                channel=channel
            )

        # add player to the queue
        QueuePlayer.objects.create(
            queue=queue,
            player=player
        )

        return queue

    @staticmethod
    def balance_queue(queue):
        players = [(p.name, p.ladder_mmr) for p in queue.players.all()]

        result = BalanceResultManager.balance_teams(players)

        queue.balance = result.answers.first()
        queue.save()

    @staticmethod
    def balance_str(balance: BalanceAnswer):
        host = os.environ.get('BASE_URL', 'localhost:8000')
        url = reverse('balancer:balancer-answer', args=(balance.id,))
        url = '%s%s' % (host, url)

        # find out who's undergdog
        teams = balance.teams
        underdog = None
        if teams[1]['mmr'] - teams[0]['mmr'] >= MatchManager.underdog_diff:
            underdog = 0
        elif teams[0]['mmr'] - teams[1]['mmr'] >= MatchManager.underdog_diff:
            underdog = 1

        result = '```\n'
        for i, team in enumerate(balance.teams):
            player_names = [p[0] for p in team['players']]
            result += f'Team {i + 1} {"↡" if i == underdog else " "} ' \
                      f'(avg. {team["mmr"]}): ' \
                      f'{" | ".join(player_names)}\n'

        result += '\nLadder MMR: \n'
        for i, team in enumerate(balance.teams):
            player_mmrs = [str(p[1]) for p in team['players']]
            result += f'Team {i + 1} {"↡" if i == underdog else " "} ' \
                      f'(avg. {team["mmr"]}): ' \
                      f'{" | ".join(player_mmrs)}\n'

        result += f'\n{url}'
        result += '```'

        return result

    @staticmethod
    def queue_str(q: LadderQueue):
        players = q.players.all()
        avg_mmr = round(mean(p.ladder_mmr for p in players))
        return f'```\n' + \
               f'Queue #{q.id}\n' + \
               f'Min MMR: {q.min_mmr}\n' + \
               f'Players: {q.players.count()} (' + \
               f' | '.join(f'{p.name}-{p.ladder_mmr}' for p in players) + ')\n\n' + \
               f'Avg. MMR: {avg_mmr} {"LUL" if avg_mmr < 4500 else ""} \n' + \
               f'```\n'

    @staticmethod
    def get_player_by_name(name):
        player = Player.objects.filter(name__iexact=name).first()

        # if exact match not found, try to guess player name
        if not player:
            player = Player.objects.filter(name__istartswith=name).first()
        if not player:
            player = Player.objects.filter(name__contains=name).first()

        return player

    def player_mention(self, player):
        discord_id = int(player.discord_id) if player.discord_id else 0
        player_discord = self.bot.get_user(discord_id)
        mention = player_discord.mention if player_discord else player.name

        return mention
