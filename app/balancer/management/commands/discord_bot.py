import asyncio
import itertools
import re
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean

import discord
import timeago
from discord.ext import tasks
from django.core.management.base import BaseCommand
import os

from django.core.urlresolvers import reverse
from django.db.models import Q, Count, Prefetch, Case, When, F

from app.balancer.managers import BalanceResultManager
from app.balancer.models import BalanceAnswer
from app.ladder.managers import MatchManager
from app.ladder.models import Player, LadderSettings, LadderQueue, QueuePlayer, QueueChannel, MatchPlayer, \
    RolesPreference, DiscordChannels, DiscordPoll


class Command(BaseCommand):
    def __init__(self):
        super().__init__()
        self.bot = None
        self.polls_channel = None
        self.last_seen = defaultdict(datetime.now)  # to detect afk players

        self.poll_reaction_funcs = {
            'DraftMode': self.on_draft_mode_reaction,
            'EliteMMR': self.on_elite_mmr_reaction,
            'Faceit': self.on_faceit_reaction,
        }

    def handle(self, *args, **options):
        bot_token = os.environ.get('DISCORD_BOT_TOKEN', '')

        self.bot = discord.Client()

        @self.bot.event
        async def on_ready():
            print(f'Logged in: {self.bot.user} {self.bot.user.id}')

            polls_channel = DiscordChannels.get_solo().polls
            self.polls_channel = self.bot.get_channel(polls_channel)

            await self.setup_poll_messages()
            queue_afk_check.start()

        @self.bot.event
        async def on_message(msg):
            self.last_seen[msg.author.id] = datetime.now()

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

        @self.bot.event
        async def on_reaction_add(reaction, user):
            self.last_seen[user.id] = datetime.now()

        @self.bot.event
        async def on_raw_reaction_add(payload):
            channel = self.bot.get_channel(payload.channel_id)
            message = await channel.fetch_message(payload.message_id)
            user = self.bot.get_user(payload.user_id)

            if user.bot:
                return

            poll = DiscordPoll.objects.filter(message_id=message.id).first()
            # if not a poll message, ignore reaction
            if not poll:
                return

            # if player is unknown, remove reaction
            try:
                player = Player.objects.get(discord_id=payload.user_id)
            except Player.DoesNotExist:
                print('unknown')
                for reaction in message.reactions:
                    await reaction.remove(user)
                return

            # remove other reactions by this user from this message
            for r in message.reactions:
                if r.emoji != payload.emoji.name:
                    await r.remove(user)

            # call reaction processing function
            await self.poll_reaction_funcs[poll.name](message, user, player)

        @self.bot.event
        async def on_raw_reaction_remove(payload):
            channel = self.bot.get_channel(payload.channel_id)
            message = await channel.fetch_message(payload.message_id)
            user = self.bot.get_user(payload.user_id)

            poll = DiscordPoll.objects.filter(message_id=message.id).first()
            # if not a poll message, ignore reaction
            if not poll:
                return

            # call reaction processing function
            await self.poll_reaction_funcs[poll.name](message, user)

        @tasks.loop(minutes=5)
        async def queue_afk_check():
            # TODO: it would be good to do here
            #  .select_related(`player`, `queue`, `queue__channel`)
            #  but this messes up with itertools.groupby.
            #  Need to measure speed here and investigate.
            players = QueuePlayer.objects.filter(queue__active=True)
            # group players by channel
            players = itertools.groupby(players, lambda x: x.queue.channel)

            for channel, qp_list in players:
                channel_players = [qp.player for qp in qp_list]

                channel = self.bot.get_channel(channel.discord_id)
                await self.channel_check_afk(channel, channel_players)

        self.bot.run(bot_token)

    async def bot_cmd(self, msg):
        command = msg.content.split(' ')[0].lower()

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
            '!afk-ping': self.afk_ping_command,
            '!afkping': self.afk_ping_command,
            '!role': self.role_command,
            '!roles': self.role_command,
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
                'Format: `!register username mmr dota_id`. Example: \n' 
                '```\n'
                '!register Uvs 3000 444510529\n'
                '```'
            )
            return

        if not 0 <= mmr < 10000:
            await msg.channel.send('Haha, very funny. :thinking:')
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
        url = reverse('ladder:player-overview', args=(player.slug,))
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
            f'Roles: {Command.roles_str(player.roles)}\n\n'
            f'{player.description or ""}\n'
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

        # TODO: this is a separate function
        if queue.players.count() == 10:
            Command.balance_queue(queue)  # todo move this to QueuePlayer signal

            balance_str = ''
            if LadderSettings.get_solo().draft_mode == LadderSettings.AUTO_BALANCE:
                balance_str = f'Proposed balance: \n' + \
                              Command.balance_str(queue.balance)

            await msg.channel.send(
                f'\nQueue is full! {balance_str} \n' +
                f' '.join(self.player_mention(p) for p in queue.players.all()) +
                f'\nYou have 5 min to join the lobby.'
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

        # TODO: this is a separate function
        if queue.players.count() == 10:
            Command.balance_queue(queue)

            balance_str = ''
            if LadderSettings.get_solo().draft_mode == LadderSettings.AUTO_BALANCE:
                balance_str = f'Proposed balance: \n' + \
                              Command.balance_str(queue.balance)

            await msg.channel.send(
                f'\nQueue is full! {balance_str} \n' +
                f' '.join(self.player_mention(p) for p in queue.players.all()) +
                f'\nYou have 5 min to join the lobby.'
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

    async def afk_ping_command(self, msg, **kwargs):
        command = msg.content
        player = kwargs['player']
        print(f'\n!afk_ping command:\n{command}')

        try:
            mode = command.split(' ')[1]
        except IndexError:
            mode = ''

        if mode.lower() in ['on', 'off']:
            player.queue_afk_ping = True if mode.lower() == 'on' else False
            player.save()
            await msg.channel.send('Aye aye, captain')
        else:
            await msg.channel.send(
                f'`{player.name}`, you current mode is `{"ON" if player.queue_afk_ping else "OFF"}`. '
                f'Available modes: \n'
                f'```\n'
                f'!afk-ping ON   - will ping you before kicking for afk.\n'
                f'!afk-ping OFF  - will kick you for afk without pinging.\n'
                f'```'
            )

    async def role_command(self, msg, **kwargs):
        command = msg.content
        player = kwargs['player']
        print(f'\n!role command from {player}:\n{command}')

        roles = player.roles
        args = command.split(' ')[1:]

        if len(args) == 5:
            # full roles format; check that we have 5 numbers from 1 to 5
            try:
                args = [int(x) for x in args]
                if any(not 0 < x < 6 for x in args):
                    raise ValueError
            except ValueError:
                await msg.channel.send('Haha, very funny :thinking:')
                return

            # args are fine
            roles.carry = args[0]
            roles.mid = args[1]
            roles.offlane = args[2]
            roles.pos4 = args[3]
            roles.pos5 = args[4]
        elif len(args) == 2:
            # !role mid 4  format
            try:
                role = args[0]
                value = int(args[1])
                if not 0 < value < 6:
                    raise ValueError

                if role in ['carry', 'pos1']:
                    roles.carry = value
                elif role in ['mid', 'midlane', 'pos2']:
                    roles.mid = value
                elif role in ['off', 'offlane', 'pos3']:
                    roles.offlane = value
                elif role in ['pos4']:
                    roles.pos4 = value
                elif role in ['pos5']:
                    roles.pos5 = value
                elif role in ['core']:
                    roles.carry = roles.mid = roles.offlane = value
                elif role in ['sup', 'supp', 'support']:
                    roles.pos4 = roles.pos5 = value
                else:
                    raise ValueError  # wrong role name
            except ValueError:
                await msg.channel.send('Haha, very funny :thinking:')
                return
        elif len(args) == 0:
            # !role command without args, show current role prefs
            await msg.channel.send(
                f'Current role prefs for `{player.name}`: \n'
                f'```\n{Command.roles_str(roles)}\n```'
            )
            return
        else:
            # wrong format, so just show help message
            await msg.channel.send(
                'This command sets your comfort score for a given role, from 1 to 5. '
                'Usage examples: \n'
                '```\n'
                '!role mid 5  - you prefer to play mid very much;\n'
                '!role pos5 2  - you don\'t really want to play hard support;\n'
                '!role supp 1  - you totally don\'t want to play any support (pos4 or pos5);\n\n'
                '!role 1 4 2 5 3  - set all roles in one command; this means carry=1, mid=4, off=3, pos4=5, pos5=2;\n'
                '\n```\n'
                'Role names: \n'
                '```\n'
                'carry/pos1, mid/midlane/pos2, off/offlane/pos3, pos4, pos5\n'
                'core  - combines carry, mid and off\n'
                'sup/supp/support  - combines pos4 and pos5\n'
                '\n```'
            )
            return

        roles.save()
        await msg.channel.send(
            f'New role prefs for `{player.name}`: \n'
            f'```\n{Command.roles_str(roles)}\n```'
        )

    @staticmethod
    def add_player_to_queue(player, channel):
        # get an available active queue
        queue = LadderQueue.objects\
            .filter(active=True)\
            .annotate(Count('players'))\
            .filter(players__count__lt=10, channel=channel)\
            .order_by('-players__count')\
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
        players = list(queue.players.all())
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
            if team['role_score_sum']:
                # this is balance with roles
                player_names = [f'{i+1}. {p[0]}' for i, p in enumerate(team['players'])]
            else:
                # balance without roles
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
               f'Avg. MMR: {avg_mmr} {"LUL" if avg_mmr < 4000 else ""} \n' + \
               f'```\n'

    @staticmethod
    def roles_str(roles: RolesPreference):
        return f'carry: {roles.carry} | mid: {roles.mid} | off: {roles.offlane} | ' + \
               f'pos4: {roles.pos4} | pos5: {roles.pos5}'

    @staticmethod
    def get_player_by_name(name):
        player = None

        # check if name is a mention
        match = re.match(r'<@!?([0-9]+)>$', name)
        if match:
            return Player.objects.filter(discord_id=match.group(1)).first()

        # not a mention, proceed normally
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

    async def channel_check_afk(self, channel: discord.TextChannel, players):
        def last_seen(p):
            return self.last_seen[int(p.discord_id or 0)]

        def afk_filter(players, allowed_time):
            t = timedelta(minutes=allowed_time)
            afk = [p for p in players if datetime.now() - last_seen(p) > t]
            return afk

        afk_allowed_time = LadderSettings.get_solo().afk_allowed_time

        afk_list = afk_filter(players, afk_allowed_time)
        if not afk_list:
            return

        ping_list = [p for p in afk_list if p.queue_afk_ping]
        if ping_list:
            afk_response_time = LadderSettings.get_solo().afk_response_time

            msg = await channel.send(
                " ".join(self.player_mention(p) for p in ping_list) +
                f"\nIt's been a while. React if you are still around. " +
                f"You have `{afk_response_time} min`.\n"
            )
            await msg.add_reaction('👌')
            await asyncio.sleep(afk_response_time * 60)

            # players who not responded
            afk_list = afk_filter(afk_list, afk_response_time)

        if not afk_list:
            return

        deleted, _ = QueuePlayer.objects \
            .filter(player__in=afk_list, queue__active=True) \
            .delete()

        if deleted > 0:
            await channel.send(
                'Purge all heretics from the queue! For the Emperor! ' +
                'These players were burned at stake: \n' +
                '```\n' +
                ' | '.join(p.name for p in afk_list) +
                '\n```'
            )

    async def setup_poll_messages(self):
        polls = ['Welcome', 'DraftMode', 'EliteMMR', 'Faceit']

        channel = self.polls_channel

        async def get_poll_message(poll):
            try:
                message_id = DiscordPoll.objects.get(name=poll).message_id
                return await channel.fetch_message(message_id)
            except (DiscordPoll.DoesNotExist, discord.NotFound):
                return None

        # remove all messages but polls
        db_messages = DiscordPoll.objects.values_list('message_id', flat=True)
        await channel.purge(check=lambda x: x.id not in db_messages)

        # create poll messages that are not already present
        poll_msg = {}
        for p in polls:
            msg = await get_poll_message(p)
            if not msg:
                msg = await channel.send(p)
                DiscordPoll.objects.update_or_create(name=p, defaults={
                    'name': p,
                    'message_id': msg.id
                })
            poll_msg[p] = msg

        await self.polls_welcome_show()
        await self.draft_mode_poll_show(poll_msg['DraftMode'])
        await self.elite_mmr_poll_show(poll_msg['EliteMMR'])
        await self.faceit_poll_show(poll_msg['Faceit'])

    async def polls_welcome_show(self):
        text = 'Hello, friends!\n\n' + \
               'Here you can vote for inhouse settings.\n\n' + \
               'These polls are directly connected to our system.' + \
               '\n\n.'
        msg_id = DiscordPoll.objects.get(name='Welcome').message_id
        msg = await self.polls_channel.fetch_message(msg_id)
        await msg.edit(content=text)

    async def draft_mode_poll_show(self, message):
        mode = LadderSettings.get_solo().draft_mode
        mode = LadderSettings.DRAFT_CHOICES[mode][1]

        text = f'\n-------------------------------\n' + \
               f'**DRAFT MODE**\n' + \
               f'-------------------------------\n' + \
               f'Current mode: **{mode}**\n\n' + \
               f'This sets the default draft mode for inhouse games.\n\n' + \
               f':man_red_haired: - player draft;\n' + \
               f':robot: - auto balance;\n\n' + \
               f'Players with 5+ inhouse games can vote. \n' + \
               f'-------------------------------'

        await message.edit(content=text)
        await message.add_reaction('👨‍🦰')
        await message.add_reaction('🤖')

    async def on_draft_mode_reaction(self, message, user, player=None):
        # if player is not eligible for voting, remove his reactions
        if player and player.matchplayer_set.count() < 5:
            for r in message.reactions:
                await r.remove(user)
            return

        # refresh message
        message = await self.polls_channel.fetch_message(message.id)

        # calculate votes
        votes_ab = discord.utils.get(message.reactions, emoji='🤖').count
        votes_pd = discord.utils.get(message.reactions, emoji='👨‍🦰').count

        # update settings
        settings = LadderSettings.get_solo()
        if votes_ab > votes_pd:
            settings.draft_mode = LadderSettings.AUTO_BALANCE
        elif votes_pd > votes_ab:
            settings.draft_mode = LadderSettings.PLAYER_DRAFT
        settings.save()

        # redraw poll message
        await self.draft_mode_poll_show(message)

    async def elite_mmr_poll_show(self, message):
        q_channel = QueueChannel.objects.get(name='High MMR queue')

        text = f'\n-------------------------------\n' + \
               f'**ELITE QUEUE MMR**\n' + \
               f'-------------------------------\n' + \
               f'Current MMR floor: **{q_channel.min_mmr}**\n\n' + \
               f'🦀 - 4000;\n' + \
               f'👶 - 4500;\n' + \
               f'💪 - 5000;\n\n' + \
               f'Only 5000+ players can vote. \n' + \
               f'-------------------------------'

        await message.edit(content=text)
        await message.add_reaction('🦀')
        await message.add_reaction('👶')
        await message.add_reaction('💪')

    async def on_elite_mmr_reaction(self, message, user, player=None):
        # if player is not eligible for voting, remove his reactions
        if player and player.ladder_mmr < 5000:
            for r in message.reactions:
                await r.remove(user)
            return

        # refresh message
        message = await self.polls_channel.fetch_message(message.id)

        # calculate votes
        votes_4000 = discord.utils.get(message.reactions, emoji='🦀').count
        votes_4500 = discord.utils.get(message.reactions, emoji='👶').count
        votes_5000 = discord.utils.get(message.reactions, emoji='💪').count

        # update settings
        q_channel = QueueChannel.objects.get(name='High MMR queue')
        if votes_4500 < votes_4000 > votes_5000:
            q_channel.min_mmr = 4000
        elif votes_4000 < votes_4500 > votes_5000:
            q_channel.min_mmr = 4500
        elif votes_4000 < votes_5000 > votes_4500:
            q_channel.min_mmr = 5000
        q_channel.save()

        # redraw poll message
        await self.elite_mmr_poll_show(message)

    async def faceit_poll_show(self, message):
        text = f'\n-------------------------------\n' + \
               f'**FACEIT**\n' + \
               f'-------------------------------\n' + \
               f'Should we go back to Faceit?\n\n' + \
               f'🇾 - yes;\n' + \
               f'🇳 - no;\n\n' + \
               f'This poll has no effect and is here to measure player sentiment. \n' + \
               f'-------------------------------'

        await message.edit(content=text)
        await message.add_reaction('🇾')
        await message.add_reaction('🇳')

    async def on_faceit_reaction(self, message, user, player=None):
        pass
