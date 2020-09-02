import discord
from django.core.management.base import BaseCommand
import os

from django.core.urlresolvers import reverse
from django.db.models import Q, Count

from app.balancer.managers import BalanceResultManager
from app.balancer.models import BalanceAnswer
from app.ladder.managers import PlayerManager
from app.ladder.models import Player, LadderSettings, LadderQueue, QueuePlayer, QueueChannel


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
            print(f'Got message from {msg.author} ({msg.author.id}): {msg.content}')

            if msg.author.bot:
                return

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
            '!q+': self.join_queue_command,
            '!q-': self.leave_queue_command,
            '!q': self.show_queues_command,
            '!add': self.add_to_queue_command,
            '!kick': self.kick_from_queue_command,
        }
        free_for_all = ['!register']
        staff_only = ['!vouch', '!add', '!kick']

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

        await msg.channel.send(
            f"""Welcome to the ladder, {name}! 
            \nYou need to get vouched before you can play. Wait for inhouse staff to review your signup. 
            \nYou can ping their lazy asses if it takes too long ;)"""
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

        try:
            player = Player.objects.get(name__iexact=name)
        except Player.DoesNotExist:
            await msg.channel.send(f'{name}: I don\'t know him')
            return

        player.vouched = True
        player.save()

        player_discord = self.bot.get_user(int(player.discord_id))
        await msg.channel.send(
            f'{player_discord.mention} has been vouched. He can play now!'
        )

    async def whois_command(self, msg, **kwargs):
        command = msg.content
        print()
        print('Whois command:')
        print(command)

        try:
            name = command.split(None, 1)[1]
        except (IndexError, ValueError):
            return

        try:
            player = Player.objects.get(name=name)
        except Player.DoesNotExist:
            await msg.channel.send(f'{name}: I don\'t know him')
            return

        match_count = player.matchplayer_set.filter(
            match__season=LadderSettings.get_solo().current_season
        ).count()

        correlation = PlayerManager.ladder_to_dota_mmr(player.ladder_mmr)

        opendota = f'https://www.opendota.com/players/{player.dota_id}'

        await msg.channel.send(
            f'```\n'
            f'{player.name}\n'
            f'MMR: {player.dota_mmr}\n'
            f'Opendota: {opendota}\n\n'
            f'Ladder MMR: {player.ladder_mmr} (corr. {correlation})\n'
            f'Score: {player.score}\n'
            f'Games: {match_count}\n\n'
            f'Vouched: {"yes" if player.vouched else "no"}\n'
            f'```'
        )

    async def join_queue_command(self, msg, **kwargs):
        command = msg.content
        player = kwargs['player']
        print(f'Join command from {player}:\n {command}')

        # check if this is a queue channel
        try:
            channel = QueueChannel.objects.get(discord_id=msg.channel.id)
        except QueueChannel.DoesNotExist:
            return

        # check that player has enough MMR
        if player.dota_mmr < channel.min_mmr:
            await msg.channel.send('Your dick is too small. Grow a bigger one.')
            return

        # check that player is not in a queue already
        if player.ladderqueue_set.filter(active=True):
            await msg.channel.send('Already queued, friend.')
            return

        queue = Command.add_player_to_queue(player, channel)

        await msg.channel.send(
            f'{player} joined inhouse queue #{queue.id}.\n' +
            Command.queue_str(queue)
        )

        if queue.players.count() == 10:
            Command.balance_queue(queue)  # todo move this to QueuePlayer signal
            await msg.channel.send('\nQueue is full! Proposed balance: \n' +
                                   Command.balance_str(queue.balance))

    async def leave_queue_command(self, msg, **kwargs):
        command = msg.content
        player = kwargs['player']
        print(f'Leave command from {player}:\n {command}')

        QueuePlayer.objects.filter(player=player, queue__active=True).delete()

        await msg.channel.send(f'{player} left the queue.\n')

    async def show_queues_command(self, msg, **kwargs):
        queues = LadderQueue.objects.filter(active=True)
        await msg.channel.send(
            ''.join(Command.queue_str(q) for q in queues)
        )

    async def add_to_queue_command(self, msg, **kwargs):
        command = msg.content
        print(f'add_to_queue command from:\n {command}')

        try:
            name = command.split(None, 1)[1]
        except (IndexError, ValueError):
            return

        # get player from db
        try:
            player = Player.objects.get(name__iexact=name)
        except Player.DoesNotExist:
            await msg.channel.send(f'{name}: I don\'t know him')
            return

        # check that player is not in a queue already
        if player.ladderqueue_set.filter(active=True):
            await msg.channel.send(f'{player} is already in a queue')
            return

        # check if this is a queue channel
        try:
            channel = QueueChannel.objects.get(discord_id=msg.channel.id)
        except QueueChannel.DoesNotExist:
            return

        queue = Command.add_player_to_queue(player, channel)

        player_discord = self.bot.get_user(int(player.discord_id))
        mention = player_discord.mention if player_discord else player.name
        await msg.channel.send(
            f'By a shameless abuse of power {msg.author.name} '
            f'forcefully added {mention} to the inhouse queue. Have fun! ;)'
        )

        if queue.players.count() == 10:
            Command.balance_queue(queue)
            await msg.channel.send('\nQueue is full! Proposed balance: \n' +
                                   Command.balance_str(queue.balance))

    async def kick_from_queue_command(self, msg, **kwargs):
        command = msg.content
        print(f'Kick command:\n {command}')

        try:
            name = command.split(None, 1)[1]
        except (IndexError, ValueError):
            return

        # get player from db
        try:
            player = Player.objects.get(name__iexact=name)
        except Player.DoesNotExist:
            await msg.channel.send(f'{name}: I don\'t know him')
            return

        QueuePlayer.objects.filter(player=player, queue__active=True).delete()

        player_discord = self.bot.get_user(int(player.discord_id))
        mention = player_discord.mention if player_discord else player.name
        await msg.channel.send(f'{mention} was kicked from the queue.')

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
        answer_num = 1

        url = reverse('balancer:balancer-result', args=(result.id,))
        host = os.environ.get('BASE_URL', 'localhost:8000')

        url = '%s%s?page=%s' % (host, url, answer_num)

        queue.balance = result.answers.all()[answer_num - 1]
        queue.save()

    @staticmethod
    def balance_str(balance: BalanceAnswer):
        result = '```\n'
        for i, team in enumerate(balance.teams):
            player_names = [p[0] for p in team['players']]
            result += f'Team {i+1} (avg. {team["mmr"]}): {" | ".join(player_names)}\n'
        result += '```'

        return result

    @staticmethod
    def queue_str(q: LadderQueue):
        return f'```\n' + \
               f'Queue #{q.id}\n' + \
               f'Min MMR: {q.min_mmr}\n' + \
               f'Players: {q.players.count()} (' + \
               f' | '.join(p.name for p in q.players.all()) + ')\n\n' + \
               f'```\n'
