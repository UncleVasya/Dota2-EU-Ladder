import asyncio
import datetime
import itertools
import re
from collections import defaultdict, deque
from datetime import timedelta
import random
from statistics import mean

import discord
from discord import Button, ButtonStyle, user
import pytz
import timeago
from discord.ext import tasks
from django.core.management.base import BaseCommand
import os

from django.core.urlresolvers import reverse
from django.db.models import Q, Count, Prefetch, Case, When, F
from django.utils import timezone

from app.balancer.managers import BalanceResultManager, BalanceAnswerManager
from app.balancer.models import BalanceAnswer
from app.ladder.managers import MatchManager, QueueChannelManager
from app.ladder.models import Player, LadderSettings, LadderQueue, QueuePlayer, QueueChannel, MatchPlayer, \
    RolesPreference, DiscordChannels, DiscordPoll, ScoreChange


def is_player_registered(msg, dota_id, name):
    # check if we can register this player
    if Player.objects.filter(Q(discord_id=msg.author.id) | Q(dota_id=dota_id)).exists():
        return True
    if Player.objects.filter(name__iexact=name).exists():
        return True


class Command(BaseCommand):
    REGISTER_MSG_TEXT = "Odpowiedz na tę wiadomość podając: MMR, SteamID"

    def __init__(self):
        super().__init__()
        self.bot = None
        self.polls_channel = None
        self.queues_channel = None
        self.status_message = None  # status msg in queues channel
        self.status_responses = deque(maxlen=3)
        self.last_seen = defaultdict(timezone.now)  # to detect afk players
        self.kick_votes = defaultdict(lambda: defaultdict(set))
        self.queued_players = set()
        self.last_queues_update = timezone.now()

        # cached discord models
        self.queue_messages = {}

        self.poll_reaction_funcs = {
            'DraftMode': self.on_draft_mode_reaction,
            'EliteMMR': self.on_elite_mmr_reaction,
            'Faceit': self.on_faceit_reaction,
        }

    def handle(self, *args, **options):
        bot_token = os.environ.get('DISCORD_BOT_TOKEN', '')

        intents = discord.Intents.default()
        intents.members = True

        self.bot = discord.Client(intents=intents)

        @self.bot.event
        async def on_ready():
            print(f'Logged in: {self.bot.user} {self.bot.user.id}')

            polls_channel = DiscordChannels.get_solo().polls
            self.polls_channel = self.bot.get_channel(polls_channel)

            queues_channel = DiscordChannels.get_solo().queues
            self.queues_channel = self.bot.get_channel(queues_channel)

            # await self.setup_poll_messages()

            await self.purge_queue_channels()
            await self.setup_queue_messages()

            queue_afk_check.start()
            update_queues_shown.start()
            clear_queues_channel.start()
            sky_stock_joke.start()

            activate_queue_channels.start()
            deactivate_queue_channels.start()

        async def on_register_form_answer(message):
            # Check if the message is a response to the form
            if message.author != self.bot.user and message.reference:
                original_message = await message.channel.fetch_message(message.reference.message_id)
                if original_message.author == self.bot.user:
                    fields = message.content.split(',')
                    fields.append(message.author.name)
                    print(f"Fields: {fields}")
                    mmr = int(fields[0])
                    steam_id = str(int(fields[1]))
                    discord_main = fields[2]
                    await self.register_new_player(message, discord_main, mmr, steam_id)

        @self.bot.event
        async def on_message(msg):
            await on_register_form_answer(msg)
            self.last_seen[msg.author.id] = timezone.now()

            if not QueueChannel.objects.filter(discord_id=msg.channel.id).exists() \
               and not (msg.channel.id == DiscordChannels.get_solo().chat):
                return
            if msg.author.bot:
                return

            msg.content = " ".join(msg.content.split())
            if msg.content.startswith('!'):
                # looks like this is a bot command
                await self.bot_cmd(msg)

        @self.bot.event
        async def on_button_click(interaction: discord.Interaction, button):
            button_parts = button.custom_id.split('-')
            if len(button_parts) != 2:
                print("Invalid button custom_id format.")
                return

            type, value = button_parts
            print(button_parts)

            player = Command.get_player_by_name(interaction.user.name)

            if not player and type != 'register_form':
                await interaction.channel.send(f'`{interaction.user.name}`: I don\'t know him')
                return

            if type == 'green':
                q_channel = QueueChannel.objects.filter(discord_msg=value).first()

                _, _, response = await self.player_join_queue(player, q_channel)
                embed = discord.Embed(title='Zbierają się do bitwy!',
                                      description=response,
                                      color=discord.Color.green())
                # await interaction.respond(embed=embed, allowed_mentions=None, delete_after=5)
                await interaction.edit(embed=embed)

            elif type == 'red':
                response = await self.player_leave_queue(player, interaction.message)
                embed = discord.Embed(title='Gracz uchyla się od gry...',
                                      description=response,
                                      color=discord.Color.red())
                await interaction.edit(embed=embed)

            elif type == 'vouch':
                vouched_player = Command.get_player_by_name(value)

                if not player.bot_access:
                    await interaction.defer()
                    return

                if not vouched_player:
                    embed = discord.Embed(title='Błąd nazwy gracza do !vouch',
                                          color=discord.Color.red())
                    await interaction.message.edit(embed=embed)
                    return

                await self.player_vouched(vouched_player)
                embed = discord.Embed(title='Wiwat! Gracz zatwierdzony!',
                                      description=value + ' zatwierdzony przez ' + player.name,
                                      color=discord.Color.blue())
                await interaction.edit(embed=embed)
                await self.purge_buttons_from_msg(interaction.message)


            elif type == "register_form":
                if player:
                    await interaction.defer()

                    return

                text = f"""Hej, {self.unregistered_mention(interaction.author)},
                     \npodaj swój MMR, STEAM_ID w wątku do tej wiadomości"""

                await interaction.author.send(text)

                await interaction.defer()

                return

            await self.queues_show()

        @tasks.loop(minutes=5)
        async def queue_afk_check():
            # TODO: it would be good to do here
            #  .select_related(`player`, `queue`, `queue__channel`)
            #  but this messes up with itertools.groupby.
            #  Need to measure speed here and investigate.
            players = QueuePlayer.objects\
                .filter(queue__active=True)\
                .annotate(Count('queue__players'))\
                .filter(queue__players__count__lt=10)

            # group players by channel
            players = itertools.groupby(players, lambda x: x.queue.channel)

            for channel, qp_list in players:
                channel_players = [qp.player for qp in qp_list]

                channel = self.bot.get_channel(channel.discord_id)
                await self.channel_check_afk(channel, channel_players)

        @tasks.loop(seconds=30)
        async def update_queues_shown():
            queued_players = [qp for qp in QueuePlayer.objects.filter(queue__active=True)]
            queued_players = set(qp.player.discord_id for qp in queued_players)

            outdated = timezone.now() - self.last_queues_update > timedelta(minutes=5)
            if queued_players != self.queued_players or outdated:
                await self.queues_show()

        @tasks.loop(minutes=1)
        async def sky_stock_joke():
            import yfinance as yf
            from app.stock_joke.models import StockJokeSettings
            from app.stock_joke.models import StockBuyer

            settings = StockJokeSettings.get_solo()
            if not settings.enabled:
                return

            try:
                ticker_data = yf.download(tickers=settings.stock_ticker, period='1d', interval='1m')
                ticker_price = ticker_data['Close'][-1]
                print('ticker price: ', ticker_price)

                guild = self.bot.get_guild(settings.discord_server_id)
                green_role = discord.utils.get(guild.roles, id=settings.greed_role_id)
                red_role = discord.utils.get(guild.roles, id=settings.red_role_id)

                for buyer in StockBuyer.objects.all():
                    buyer_discord = discord.utils.get(self.bot.get_all_members(), id=buyer.discord_id)
                    entry_price = buyer.entry_price
                    if ticker_price <= entry_price:
                        await buyer_discord.remove_roles(green_role)
                        await buyer_discord.add_roles(red_role)

                        pct_loss = (entry_price - ticker_price) / entry_price * 100
                        await buyer_discord.edit(nick=f'{buyer.name} is red -{pct_loss:.2f}%')
                    else:
                        await buyer_discord.remove_roles(red_role)
                        await buyer_discord.add_roles(green_role)

                        pct_gain = (ticker_price - entry_price) / entry_price * 100
                        await buyer_discord.edit(nick=f'{buyer.name} is green +{pct_gain:.2f}%')
            except:
                pass  # avoid crashing on lack of permissions or yahoo finance throttle

        @tasks.loop(minutes=1)
        async def activate_queue_channels():
            dt = timezone.localtime(timezone.now(), pytz.timezone('CET'))

            # at 24:00 activate qchannels that should be active today
            if dt.hour == 0 and dt.minute == 0:
                print('Activating queue channels.')
                QueueChannelManager.activate_qchannels()
                await self.setup_queue_messages()

        @tasks.loop(minutes=1)
        async def deactivate_queue_channels():
            dt = timezone.localtime(timezone.now(), pytz.timezone('CET'))

            # at 08:00 deactivate qchannels that should be inactive today
            if dt.hour == 8 and dt.minute == 0:
                print('Deactivating queue channels')
                QueueChannelManager.deactivate_qchannels()
                await self.setup_queue_messages()

        """
        This task removes unnecessary messages (status and pings);
        This is done to make channel clear and also to highlight it 
        when new status message appears after some time.
        """
        @tasks.loop(minutes=5)
        async def clear_queues_channel():
            channel = DiscordChannels.get_solo().queues
            channel = self.bot.get_channel(channel)

            db_messages = QueueChannel.objects.filter(active=True).values_list('discord_msg', flat=True)

            def should_remove(msg):
                msg_time = msg.edited_at or msg.created_at
                lifetime = timedelta(minutes=5)
                outdated = timezone.now() - timezone.make_aware(msg_time) > lifetime

                return (msg.id not in db_messages) and outdated

            await channel.purge(check=should_remove)

        self.bot.run(bot_token)

    async def bot_cmd(self, msg):
        command = msg.content.split(' ')[0].lower()

        commands = self.get_available_bot_commands()
        free_for_all = ['!register', '!help', '!reg', '!r', '!jak', '!info']
        staff_only = [
            '!vouch', '!add', '!kick', '!mmr', '!ban', '!unban',
            '!set-name', '!set-mmr', '!set-dota-id', '!record-match',
            '!close',
        ]
        # TODO: do something with this, getting too big. Replace with disabled_in_chat list?
        chat_channel = [
            '!register', '!vouch', '!wh', '!who', '!whois', '!profile', '!stats', '!top',
            '!streak', '!bottom', '!bot', '!afk-ping', '!afkping', '!role', '!roles', '!recent',
            '!ban', '!unban', '!votekick', '!vk', '!set-name', 'rename' '!set-mmr',
            'adjust', '!set-dota-id', '!record-match', '!help', '!close', '!reg', '!r'
        ]

        # if this is a chat channel, check if command is allowed
        if msg.channel.id == DiscordChannels.get_solo().chat:
            if command not in chat_channel:
                return

        # if command is free for all, no other checks required
        if command in free_for_all:
            await commands[command](msg)
            return

        # get player from DB using discord id
        try:
            player = Player.objects.get(discord_id=msg.author.id)
        except Player.DoesNotExist:
            mention = self.unregistered_mention(msg.author)
            print(mention)
            await msg.channel.send(f'{mention}, not registered to use commands')
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

        if not 0 <= mmr < 12000:
            sent_message = await msg.channel.send('Haha, very funny. :thinking:')

            #TRY to set visibility to only single user - not working.
            # Get the @everyone role
            # everyone_role_id = msg.guild.default_role.id
            # # Set permissions to only allow the specified user to read the message
            # overwrite = {
            #     str(everyone_role_id): {
            #         'read_messages': False
            #     },
            #     str(msg.author.id): {
            #         'read_messages': True
            #     }
            # }
            #
            # await sent_message.edit(overwrites=overwrite)

            # await sent.edit(overwrites=overwrite)

            return

        await self.register_new_player(msg, name, mmr, dota_id)

    async def register_new_player(self, msg, name, mmr, dota_id):
        if is_player_registered(msg, dota_id, name):
            await msg.channel.send('Spoko, już z nami jesteś.')
            return

        # all is good, can register
        player = Player.objects.create(
            name=name,
            dota_mmr=mmr,
            dota_id=dota_id,
            discord_id=msg.author.id,
        )


        Player.objects.update_ranks()

        await self.player_vouched(player)

        queue_channel = DiscordChannels.get_solo().queues
        chat_channel = DiscordChannels.get_solo().chat
        channel = self.bot.get_channel(chat_channel)
        await msg.channel.send(
            f"""Witamy w Polish Dota2 Inhouse League!, `{name}`! 
               \nMożesz dołączyć do gry na kanale <#{queue_channel}>"""
        )

        await channel.send(
            f"""'Witamy nowego gracza `{name}`'"""
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

        await self.player_vouched(player)

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
            f'Rank: {player.rank_score}\n'
            f'Games: {len(player.matches)} ({wins}-{losses})\n\n'
            f'Vouched: {"yes" if player.vouched else "no"}\n'
            f'Roles: {Command.roles_str(player.roles)}\n\n'
            f'{player.description or ""}\n'
            f'```'
        )

    async def ban_command(self, msg, **kwargs):
        command = msg.content
        player = kwargs['player']
        print(f'Ban command from {player}:\n {command}')

        try:
            name = command.split(None, 1)[1]
        except (IndexError, ValueError):
            return

        player = Command.get_player_by_name(name)
        if not player:
            await msg.channel.send(f'`{name}`: I don\'t know him')
            return

        player.banned = Player.BAN_PLAYING
        player.save()

        await msg.channel.send(
            f'```\n'
            f'{player.name} has been banned.\n'
            f'```'
        )

    async def unban_command(self, msg, **kwargs):
        command = msg.content
        player = kwargs['player']
        print(f'Unban command from {player}:\n {command}')

        try:
            name = command.split(None, 1)[1]
        except (IndexError, ValueError):
            return

        player = Command.get_player_by_name(name)
        if not player:
            await msg.channel.send(f'`{name}`: I don\'t know him')
            return

        player.banned = None
        player.save()

        await msg.channel.send(
            f'```\n'
            f'{player.name} has been unbanned.\n'
            f'```'
        )

    async def join_queue_command(self, msg, **kwargs):
        command = msg.content
        player = kwargs['player']
        print(f'Join command from {player}:\n {command}')

        channel = QueueChannel.objects.get(discord_id=msg.channel.id)
        _, _, response = self.player_join_queue(player, channel)

        await msg.channel.send(response)
        await self.queues_show()

    async def attach_join_buttons_to_queue_msg(self, msg, **kwargs):
        await self.attach_buttons_to_msg(msg, [
            [
                Button(label="Wchodzę",
                       custom_id="green-" + str(msg.id),
                       style=ButtonStyle.green),
                Button(label="Out",
                       custom_id="red-" + str(msg.id),
                       style=ButtonStyle.red),
            ]
        ])

    async def attach_buttons_to_msg(self, msg, buttons, **kwargs):
        await msg.edit(components=buttons)
    
    async def purge_buttons_from_msg(self, msg):
        await msg.edit(components=[])

    async def attach_help_buttons_to_msg(self, msg):
        if is_player_registered(msg, 0, "blank"):
            await msg.channel.send('Spoko, już z nami jesteś.')
            return

        await msg.author.send(components=[
        [Button(label="!register", custom_id="register_form-"+str(msg.channel.id), style=ButtonStyle.blurple)]
    ])


    async def leave_queue_command(self, msg, **kwargs):
        command = msg.content
        player = kwargs['player']
        print(f'Leave command from {player}:\n {command}')

        await self.player_leave_queue(player, msg)
        await self.queues_show()

    async def show_queues_command(self, msg, **kwargs):
        queues = LadderQueue.objects.filter(active=True)
        if queues:
            await msg.channel.send(
                ''.join(Command.queue_str(q) for q in queues)
            )
        else:
            await msg.channel.send('No one is currently queueing.')

        await self.queues_show()

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

        channel = QueueChannel.objects.get(discord_id=msg.channel.id)
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

        await self.queues_show()

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
            await msg.channel.send(f'`{player}` is not in any queue.\n')

        await self.queues_show()

    async def votekick_command(self, msg, **kwargs):
        command = msg.content
        player = kwargs['player']
        print(f'Vote kick command from {player}:\n {command}')

        try:
            name = command.split(None, 1)[1]
        except (IndexError, ValueError):
            return

        queue = player.ladderqueue_set.filter(active=True).first()
        if not queue or queue.players.count() < 10:
            await msg.channel.send(f'`{player}`, you are not in a full queue.')
            return

        victim = Command.get_player_by_name(name)
        if not victim:
            await msg.channel.send(f'`{name}`: I don\'t know him')
            return

        if victim not in queue.players.all():
            await msg.channel.send(f'`{victim}` is not in your queue {player}.')
            return

        votes_needed = LadderSettings.get_solo().votekick_treshold
        # TODO: im memory becomes an issue, use queue and player ids instead of real objects
        votes = self.kick_votes[queue][victim]
        votes.add(player)

        voters_str = ' | '.join(player.name for player in votes)
        await msg.channel.send(
            f'```\n'
            f'{len(votes)}/{votes_needed} votes to kick {victim}.'
            f' Voters: {voters_str}\n'
            f'```'
        )

        if len(votes) >= votes_needed:
            QueuePlayer.objects \
                .filter(player=victim, queue__active=True) \
                .delete()

            del self.kick_votes[queue][victim]

            victim_discord = self.bot.get_user(int(victim.discord_id))
            mention = victim_discord.mention if victim_discord else victim.name
            await msg.channel.send(f'{mention} was Walrus Kicked from the queue.')

            await self.queues_show()

    async def mmr_command(self, msg, **kwargs):
        command = msg.content
        print(f'\n!mmr command:\n{command}')

        try:
            min_mmr = int(command.split(' ')[1])
            min_mmr = max(0, min(9000, min_mmr))
        except (IndexError, ValueError):
            return

        channel = QueueChannel.objects.get(discord_id=msg.channel.id)

        if LadderQueue.objects.filter(channel=channel, active=True).exists():
            await msg.channel.send(
                f'Cannot change MMR when there is an active queue in the channel')
            return

        channel.min_mmr = min_mmr
        channel.save()

        await msg.channel.send(f'Min MMR set to {min_mmr}')

    async def top_command(self, msg, **kwargs):
        def get_top_players(limit, bottom=False):
            season = LadderSettings.get_solo().current_season
            qs = Player.objects \
                .order_by('-score', '-ladder_mmr') \
                .filter(matchplayer__match__season=season).distinct()\
                .annotate(
                    match_count=Count('matchplayer'),
                    wins=Count(Case(
                        When(
                            matchplayer__team=F('matchplayer__match__winner'), then=1)
                    )
                    ),
                    losses=F('match_count') - F('wins'),
                )
            players = qs[:limit]
            if bottom:
                players = reversed(players.reverse())
            return players

        def player_str(p):
            # pretty format is tricky
            # TODO: let's move to discord embeds asap
            name_offset = 25 - len(p.name)
            result = f'{p.name}: {" " * name_offset} {p.score}  ' \
                     f'{p.wins}W-{p.losses}L  {p.ladder_mmr} ihMMR'

            return result

        command = msg.content
        bottom = kwargs.get('bottom', False)  # for '!bottom' command
        print(f'\n!top command:\n{command}')

        if LadderSettings.get_solo().casual_mode:
            joke_top = "this command is enabled only when Panda is top 1"
            joke_bot = "this command is disabled when Panda is on the list"
            txt = f'Play for fun! Who cares. ({joke_bot if bottom else joke_top})'
            await msg.channel.send(txt)
            return

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
        players = get_top_players(limit, bottom)
        top_str = '\n'.join(
            f'{p.rank_score:2}. {player_str(p)}' for p in players
        )
        await msg.channel.send(
            f'```{top_str} ``` \n'
            f'Full leaderboard is here: {url}'
        )

    async def bottom_command(self, msg, **kwargs):
        print(f'\n!bottom command:\n{msg.content}')

        kwargs.update({'bottom': True})
        await self.top_command(msg, **kwargs)

    async def streak_command(self, msg, **kwargs):
        command = msg.content
        player = kwargs['player']
        print(f'\n!streak command from {player}:\n{command}')

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

        mps = player.matchplayer_set.filter(match__season=LadderSettings.get_solo().current_season)
        results = ['win' if x.team == x.match.winner else 'loss' for x in mps]

        streaks = [list(g) for k, g in itertools.groupby(results)]

        if not streaks:
            await msg.channel.send(f'`{player.name}`: You didn\'t play a thing to have streak.')
            return

        streak = streaks[0]
        max_streak = max(streaks, key=len)

        await msg.channel.send(
            f'```\n'
            f'{player} streaks\n\n'
            f'Current: {len(streak)}{"W" if streak[0] == "win" else "L"}\n'
            f'Biggest: {len(max_streak)}{"W" if max_streak[0] == "win" else "L"}\n'
            f'```'
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
                f'`{player.name}`, your current mode is `{"ON" if player.queue_afk_ping else "OFF"}`. '
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
                f'Current roles for `{player.name}`: \n'
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
            f'New roles for `{player.name}`: \n'
            f'```\n{Command.roles_str(roles)}\n```'
        )

    async def recent_matches_command(self, msg, **kwargs):
        command = msg.content
        player = kwargs['player']
        print(f'\n!recent command from {player}:\n{command}')

        # possible formats:
        #   !recent
        #   !recent 10
        #   !recent jedi judas
        #   !recent jedi judas 10
        name = None
        num = 5
        try:
            params = command.split(None, 1)[1]  # get params string
            try:
                # check if matches num present
                num = int(params.split()[-1])
                name = ' '.join(params.split()[:-1])  # remove number of games, leaving only the name
            except ValueError:
                # only name is present
                name = params
        except IndexError:
            pass  # no params given, use defaults

        if name:
            player = Command.get_player_by_name(name)
            if not player:
                await msg.channel.send(f'`{name}`: I don\'t know him')
                return

        host = os.environ.get('BASE_URL', 'localhost:8000')
        url = reverse('ladder:player-overview', args=(player.slug,))
        player_url = f'{host}{url}'

        if not 0 < num < 10:
            await msg.channel.send(f'Just visit {player_url}')
            return

        mps = player.matchplayer_set.all()[:num]
        for mp in mps:
            mp.result = 'win' if mp.team == mp.match.winner else 'loss'

        def match_str(mp):
            dotabuff = f'https://www.dotabuff.com/matches/{mp.match.dota_id}'
            return f'{timeago.format(mp.match.date, timezone.now()):<15}{mp.result:<6}{dotabuff}'

        await msg.channel.send(
            f'```\n' +
            f'Last {num} matches of {player}:\n\n' +
            f'\n'.join(match_str(x) for x in mps) +
            f'\n```\n' +
            f'More on {player_url}'
        )

    # async def help_command(self, msg, **kwargs):
    #     commands_dict = self.get_available_bot_commands()
    #     keys_as_string = ', '.join(commands_dict.keys())
    #
    #     await msg.channel.send(
    #         f'```\n' +
    #         f'Lista komend:\n\n' +
    #         keys_as_string +
    #         f'\n```\n'
    #     )

    async def help_command(self, msg, **kwargs):
        commands_dict = self.get_help_commands()

        # Create a dictionary to store aliases
        aliases = {}

        master_text = ''
        # Iterate through the commands and gather aliases
        for group, texts in commands_dict.items():
            master_text += f'\n\n{group}\n'
            for key, text in texts.items():
                master_text += key + ": " + text + "\n"

        # Create a string representation of commands and aliases

        await msg.channel.send(
            f'```\n' +
            f'Lista komend:\n\n' +
            master_text +
            f'\n```\n'
        )

    async def registration_help_command(self, msg, **kwargs):
        print('jak command')
        await msg.channel.send(
            f'```\n'
            f'Instrukcja Ligi nhousowej - KROK po KROKU\n\n'
            f'1. Rejestracja do ligi ->  wpisz **!reg**\n'
            f'2. Odpowiadając botowi na wiadomość podaj swój obecny **MMR, STEAM_ID **( 2137, 12356789).\n'
            f'3. Czekaj na zatwierdzenie przez ADMINA.\n'
            f'4. Po zatwierdzeniu możesz dołączyć do kolejki na kanale <#1204430964948738110>\n'
            f'5. Regulamin ligi znajdziesz na kanale #regulamin (kanał jeszcze nie ustalony)\n\n'
            f'Powodzenia!\n'
            f'```'
        )

    async def set_name_command(self, msg, **kwargs):
        command = msg.content
        admin = kwargs['player']
        print(f'\n!set-name command from {admin}:\n{command}')

        try:
            params = command.split(None, 1)[1]  # get params string
            mention = params.split()[0]
            new_name = ' '.join(params.split()[1:])  # rest of the string is a new name
        except (IndexError, ValueError):
            await msg.channel.send(
                f'Wrong command usage. Correct example: `!set-name @Baron g4mbl3r`')
            return

        # check if name is a mention
        match = re.match(r'<@!?([0-9]+)>$', mention)
        if not match:
            await msg.channel.send(
                f'Wrong command usage. Use mention here. Correct example: `!set-name @Baron g4mbl3r`')
            return

        player = Command.get_player_by_name(mention)
        if not player:
            await msg.channel.send(f'I don\'t know him')
            return

        player.name = new_name
        player.save()
        await msg.channel.send(f'{mention} is now known as `{new_name}`')

    async def set_mmr_command(self, msg, **kwargs):
        command = msg.content
        admin = kwargs['player']
        print(f'\n!set-mmr command from {admin}:\n{command}')

        try:
            params = command.split(None, 1)[1]  # get params string
            new_mmr = int(params.split()[-1])
            name = ' '.join(params.split()[:-1])  # remove mmr, leaving only the name
        except (IndexError, ValueError):
            await msg.channel.send(
                f'Wrong command usage. Correct example: `!set-mmr Baron 6500`')
            return

        player = Command.get_player_by_name(name)
        if not player:
            await msg.channel.send(f'`{name}`: I don\'t know him')
            return

        ScoreChange.objects.create(
            player=player,
            mmr_change=(new_mmr - player.ladder_mmr),
            season=LadderSettings.get_solo().current_season,
            info=f'Admin action. MMR updated by {admin}'
        )
        await msg.channel.send(f'`{player}` is a `{new_mmr} MMR` gamer now!')

    async def set_dota_id_command(self, msg, **kwargs):
        command = msg.content
        admin = kwargs['player']
        print(f'\n!set-dota-id command from {admin}:\n{command}')

        try:
            params = command.split(None, 1)[1]  # get params string
            dota_id = params.split()[-1]
            name = ' '.join(params.split()[:-1])  # remove dota id, leaving only the name
        except (IndexError, ValueError):
            await msg.channel.send(
                f'Wrong command usage. Correct example: `!set-dota-id Nappa 111886427`')
            return

        player = Command.get_player_by_name(name)
        if not player:
            await msg.channel.send(f'I don\'t know him')
            return

        player.dota_id = dota_id
        player.save()
        await msg.channel.send(f'`{player}` dota id updated.')

    async def record_match_command(self, msg, **kwargs):
        command = msg.content
        admin = kwargs['player']
        print(f'\n!record-match command from {admin}:\n{command}')

        try:
            params = command.split(None, 1)[1]  # get params string
            winner = params.split()[0].lower()
            players = ' '.join(params.split()[1:])  # rest of the string are 10 player mentions
        except (IndexError, ValueError):
            await msg.channel.send(
                f'Wrong command usage. '
                f'Correct example: `!record-match radiant @Baron @lis ... (10 player mentions)`')
            return

        if winner not in ['radiant', 'dire']:
            await msg.channel.send(
                f'Scientists are baffled. Dota has 2 teams: `radiant` and `dire`. '
                f'You invented a third one: `{winner}`. Congratulations!')
            return

        players = re.findall(r'<@!?([0-9]+)>', players)
        players = list(dict.fromkeys(players))  # remove duplicates while preserving order

        # check if we have 10 mentions of players
        if len(players) != 10:
            await msg.channel.send(
                f'Can you count to 10? Why do we have {len(players)} unique mentions here?')
            return

        radiant = Player.objects.filter(discord_id__in=players[:5])
        dire = Player.objects.filter(discord_id__in=players[5:])

        print(f'radiant: {radiant}')
        print(f'dire: {dire}')

        # check if all mentioned players are registered as players
        if len(radiant) != 5 or len(dire) != 5:
            await msg.channel.send(
                f'Some of mentioned players are not registered. '
                f'I could tell you which ones but I won\'t.')
            return

        _radiant = [(p.name, p.ladder_mmr) for p in radiant]
        _dire = [(p.name, p.ladder_mmr) for p in dire]
        winner = 0 if winner == 'radiant' else 1

        balance = BalanceAnswerManager.balance_custom([_radiant, _dire])
        MatchManager.record_balance(balance, winner)

        await msg.channel.send(
            f'```\n' +
            f'Match recorded!\n\n' +
            f'Radiant: {", ".join([p.name for p in radiant])}\n' +
            f'Dire: {", ".join([p.name for p in dire])}\n' +
            f'\n{"Radiant" if winner == 0 else "Dire"} won.\n'
            f'\n```'
        )

    async def close_queue_command(self, msg, **kwargs):
        command = msg.content
        player = kwargs['player']
        print(f'\n!close command from {player}:\n{command}')

        try:
            qnumber = int(command.split(' ')[1])
        except (IndexError, ValueError):
            await msg.channel.send(
                f'Format: `!close QUEUE_NUMBER`. Example: `!close 454`')
            return

        try:
            queue = LadderQueue.objects.get(id=qnumber)
        except LadderQueue.DoesNotExist:
            await msg.channel.send(f'No such queue exists.')
            return

        queue.active = False
        if queue.game_start_time:  # queue that is stuck in the game
            queue.game_end_time = timezone.now()
        queue.save()

        await self.queues_show()
        await msg.channel.send(f'`Queue #{qnumber}` has been closed.')

    async def player_join_queue(self, player, channel):
        # check if player is banned
        if player.banned:
            response = f'`{player}`, you are banned.'
            return None, False, response

        # check if player is vouched
        if not player.vouched:
            response = f'`{player}`, you need to get vouched before you can play.'
            return None, False, response

        # check if player has enough MMR
        if player.filter_mmr < channel.min_mmr:
            response = f'`{player}`, your dick is too small. Grow a bigger one.'
            return None, False, response

        # check if player's mmr does not exceed limit, if there's any
        if player.filter_mmr > channel.max_mmr > 0:
            response = f'`{player}`, your dick is too big. Chop it off.'
            return None, False, response

        queue = player.ladderqueue_set.filter(
            Q(active=True) |
            Q(game_start_time__isnull=False) & Q(game_end_time__isnull=True)
        ).first()

        if queue:
            # check that player is not in this queue already
            if queue.channel == channel:
                response = f'`{player}`, already queued friend.'
                return queue, False, response

            # check that player is not already in a full queue
            if queue.players.count() == 10:
                response = f'`{player}`, you are under arrest dodging scum. Play the game.'
                return None, False, response

        # remove player from other queues
        QueuePlayer.objects\
            .filter(player=player, queue__active=True)\
            .exclude(queue__channel=channel)\
            .delete()

        queue = Command.add_player_to_queue(player, channel)

        response = f'`{player}` joined inhouse queue #{queue.id}.\n' + \
                   Command.queue_str(queue)

        # TODO: this is a separate function
        if queue.players.count() == 10:
            Command.balance_queue(queue)  # todo move this to QueuePlayer signal

            balance_str = ''
            if LadderSettings.get_solo().draft_mode == LadderSettings.AUTO_BALANCE:
                balance_str = f'Proposed balance: \n' + \
                              Command.balance_str(queue.balance)

            response += f'\nQueue is full! {balance_str} \n' + \
                        f' '.join(self.player_mention(p) for p in queue.players.all()) + \
                        f'\nYou have 5 min to join the lobby.'

        return queue, True, response

    @staticmethod
    def add_player_to_queue(player, channel):
        # TODO: this whole function should be QueueManager.add_player_to_queue()
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
                max_mmr=channel.max_mmr,
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
    def balance_str(balance: BalanceAnswer, verbose=True):
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
            if 'role_score_sum' in team:
                # this is balance with roles
                player_names = [f'{i+1}. {p[0]}' for i, p in enumerate(team['players'])]
            else:
                # balance without roles
                player_names = [p[0] for p in team['players']]
            result += f'Team {i + 1} {"↡" if i == underdog else " "} ' \
                      f'(avg. {team["mmr"]}): ' \
                      f'{" | ".join(player_names)}\n'

        if verbose:
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
    def queue_str(q: LadderQueue, show_min_mmr=True):
        players = q.players.all()
        avg_mmr = round(mean(p.ladder_mmr for p in players))

        game_str = ''
        if q.game_start_time:
            time_game = timeago.format(q.game_start_time, timezone.now())
            game_str = f'Gra ruszyła {time_game}. Oglądaj tu: {q.game_server}\n'

        suffix = LadderSettings.get_solo().noob_queue_suffix

        return f'```\n' + \
               f'Kolejka #{q.id}\n' + \
               game_str + \
               (f'Min MMR: {q.min_mmr}\n' if show_min_mmr else '\n') + \
               f'Gracze: {q.players.count()} (' + \
               f' | '.join(f'{p.name}-{p.ladder_mmr}' for p in players) + ')\n\n' + \
               f'Śr. MMR: {avg_mmr} {suffix if avg_mmr < 4000 else ""} \n' + \
               f'```'

    @staticmethod
    def roles_str(roles: RolesPreference):
        return f'carry: {roles.carry} | mid: {roles.mid} | off: {roles.offlane} | ' + \
               f'pos4: {roles.pos4} | pos5: {roles.pos5}'

    @staticmethod
    def get_player_by_name(name):
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

    def queue_full_msg(self, queue, show_balance=True):
        balance_str = ''
        auto_balance = LadderSettings.get_solo().draft_mode == LadderSettings.AUTO_BALANCE
        if auto_balance and show_balance:
            balance_str = f'Proposed balance: \n' + \
                          Command.balance_str(queue.balance)

        msg = f'\nQueue is full! {balance_str} \n' + \
              f' '.join(self.player_mention(p) for p in queue.players.all()) + \
              f'\nYou have 5 min to join the lobby.'

        return msg

    def player_mention(self, player):
        discord_id = int(player.discord_id) if player.discord_id else 0
        player_discord = self.bot.get_user(discord_id)
        mention = player_discord.mention if player_discord else player.name

        return mention

    def unregistered_mention(self, discord_user):
        user_discord = self.bot.get_user(discord_user.id)
        mention = user_discord.mention if user_discord else discord_user.name

        return mention

    async def channel_check_afk(self, channel: discord.TextChannel, players):
        def last_seen(p):
            return self.last_seen[int(p.discord_id or 0)]

        def afk_filter(players, allowed_time):
            t = timedelta(minutes=allowed_time)
            afk = [p for p in players if timezone.now() - last_seen(p) > t]
            return afk

        afk_allowed_time = LadderSettings.get_solo().afk_allowed_time

        afk_list = afk_filter(players, afk_allowed_time)
        if not afk_list:
            return

        # for now, send afk pings in chat channel
        channel = DiscordChannels.get_solo().chat
        channel = self.bot.get_channel(channel)

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

        deleted, _ = QueuePlayer.objects\
            .filter(player__in=afk_list, queue__active=True)\
            .annotate(Count('queue__players'))\
            .filter(queue__players__count__lt=10)\
            .delete()

        if deleted > 0:
            await self.queues_show()
            await channel.send(
                'Purge all heretics from the queue!\n' +
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
        q_channel = QueueChannel.objects.get(name='Elite queue')

        text = f'\n-------------------------------\n' + \
               f'**ELITE QUEUE MMR**\n' + \
               f'-------------------------------\n' + \
               f'Current MMR floor: **{q_channel.min_mmr}**\n\n' + \
               f'🦀 - 4000;\n' + \
               f'👶 - 4500;\n' + \
               f'💪 - 5000;\n\n' + \
               f'Only 4500+ players can vote. \n' + \
               f'-------------------------------'

        await message.edit(content=text)
        await message.add_reaction('🦀')
        await message.add_reaction('👶')
        await message.add_reaction('💪')

    async def on_elite_mmr_reaction(self, message, user, player=None):
        # if player is not eligible for voting, remove his reactions
        if player and player.ladder_mmr < 4500:
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
        q_channel = QueueChannel.objects.get(name='Elite queue')
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

    async def purge_queue_channels(self):
        channel = self.queues_channel
        await channel.purge()


    async def setup_queue_messages(self):
        channel = self.queues_channel

        # remove all messages but queues
        db_messages = QueueChannel.objects.filter(active=True).values_list('discord_msg', flat=True)
        await channel.purge(check=lambda x: x.id not in db_messages)

        # create queues messages that are not already present
        for q_type in QueueChannel.objects.filter(active=True):
            msg, created = await self.get_or_create_message(self.queues_channel, q_type.discord_msg)
            # await msg.add_reaction('✅')
            self.queue_messages[msg.id] = msg
            if created:
                q_type.discord_msg = msg.id
                q_type.save()

        await self.queues_show()

        # create queues messages that are not already present
        for q_type in QueueChannel.objects.filter(active=True):
            msg, created = await self.get_or_create_message(self.queues_channel, q_type.discord_msg)
            # await msg.add_reaction('✅')
            self.queue_messages[msg.id] = msg
            if created:
                q_type.discord_msg = msg.id
                q_type.save()

    async def queues_show(self):
        # remember queued players to check for changes in periodic task
        queued_players = [qp for qp in QueuePlayer.objects.filter(queue__active=True)]
        self.queued_players = set(qp.player.discord_id for qp in queued_players)
        self.last_queues_update = timezone.now()

        # show queues info
        for q_type in QueueChannel.objects.filter(active=True):
            message = self.queue_messages[q_type.discord_msg]

            min_mmr_string = f'({q_type.min_mmr}+)' if q_type.min_mmr > 0 else ''
            max_mmr_string = f'({q_type.max_mmr}-)' if q_type.max_mmr > 0 else ''
            queues = LadderQueue.objects\
                .filter(channel=q_type)\
                .filter(Q(active=True) |
                        Q(game_start_time__isnull=False) & Q(game_end_time__isnull=True))

            queues_text = '```\nPusto. Jak do tego doszło, nie wiem.\n```'
            if queues:
                queues_text =  f'\n'.join(self.show_queue(q) for q in queues)

            text = f'**{q_type.name}** {min_mmr_string} {max_mmr_string}\n' + \
                   f'{queues_text}'

            await message.edit(content=text)

            await self.attach_join_buttons_to_queue_msg(message)
            # remove reactions of players who are no longer in this queue
            # queue_players = QueuePlayer.objects\
            #     .filter(queue__channel=q_type, queue__active=True) \
            #     .values_list('player__discord_id', flat=True)

            # r = discord.utils.get(message.reactions, emoji='✅')
            # if not r:
            #     continue  # no reactions setup yet
            #
            # async for user in r.users():
            #     if not user.bot and (str(user.id) not in queue_players):
            #         await r.remove(user)

    async def on_poll_reaction_add(self, message, user, payload, player):
        poll = DiscordPoll.objects.filter(message_id=message.id).first()

        # if not a poll message, ignore reaction
        if not poll:
            return

        # remove other reactions by this user from this message
        for r in message.reactions:
            if r.emoji != payload.emoji.name:
                await r.remove(user)

        # call reaction processing function
        await self.poll_reaction_funcs[poll.name](message, user, player)

    async def on_poll_reaction_remove(self, message, user, payload, player):
        poll = DiscordPoll.objects.filter(message_id=message.id).first()

        # if not a poll message, ignore reaction
        if not poll:
            return

        # call reaction processing function
        await self.poll_reaction_funcs[poll.name](message, user)

    @staticmethod
    async def get_or_create_message(channel, msg_id):
        try:
            msg = await channel.fetch_message(msg_id)
            created = False
        except (DiscordPoll.DoesNotExist, discord.NotFound, discord.HTTPException):
            msg = await channel.send('.')
            created = True

        return msg, created

    async def update_status_message(self, text):
        channel = DiscordChannels.get_solo().queues
        channel = self.bot.get_channel(channel)

        event_time = timezone.localtime(timezone.now(), pytz.timezone('CET'))
        text = text.replace('`', '').replace('\n', '')  # remove unnecessary formatting
        self.status_responses.append(f'{event_time.strftime("%H:%M %Z"):<15}{text}')

        text = '```\n' + \
               '\n'.join(self.status_responses) + \
               '\n```'

        try:
            status_msg = discord.utils.get(self.bot.cached_messages, id=self.status_message)
            await status_msg.edit(content=text)
        except (DiscordPoll.DoesNotExist, discord.NotFound, discord.HTTPException, AttributeError):
            msg = await channel.send(text)
            self.status_message = msg.id

    def show_queue(self, q):
        q_string = self.queue_str(q, show_min_mmr=False)

        if q.players.count() == 10:
            auto_balance = LadderSettings.get_solo().draft_mode == LadderSettings.AUTO_BALANCE
            if auto_balance:
                q_string += self.balance_str(q.balance, verbose=q.active) + '\n'

        return q_string

    async def player_vouched(self, player):
        player.vouched = True
        player.save()

    async def player_leave_queue(self, player, msg):
        qs = QueuePlayer.objects \
            .filter(player=player, queue__active=True) \
            .select_related('queue') \
            .annotate(players_in_queue=Count('queue__players'))

        full_queue = next((q for q in qs if q.players_in_queue == 10), None)

        if full_queue:
            return f'`{player}`, you are in active game from Queue #{full_queue.queue.id}.\n'

        # if any(x.players_in_queue == 10 for x in qs):
        #     return f'`{player}`, you should be now playing Dota friend {x.queue.id}.\n'

        deleted, _ = qs.delete()
        if deleted > 0:
            return f'`{player}` left the queue.\n'
        else:
            return f'`{player}` you are not in this queue.\n'

    def get_help_commands(self):
        return {
            'Basic': {
                '!help': 'This command',
                '!jak/!info': 'How to REGISTER: STEP-BY-STEP',
                '!r/!reg': 'Used to register a new Player',
                '!register': 'Text mode of registration',
                '!wh/!who/!whois/!profile/!stats': 'Shows statistics about the player',
                '!top': 'Top players',
                '!bot/!bottom': 'Bottom players',
                '!streak': 'Your streak current & ath',
                '!role/!roles': 'Set your role preferences',
                '!recent': 'Show recent matches',
            },
            'Queue': {
                '!join/!q+': 'Join queue',
                '!leave/!q-': 'Leave queue',
                '!list/!q': 'List of queues',
                '!vk/!votekick': 'Vote kick player',
                '!afk-ping/!afkping': 'Ping AFK players',
            },
            'Admin': {
                '!vouch': 'Used to accept players to league(currently off)',
                '!ban': 'Ban player',
                '!unban': 'Unban player',
                '!set-mmr/!adjust': 'Set MMR of a player',
                '!set-dota-id': 'Set STEAM ID of a player'
            },
            'AdminQueue': {
                '!add': 'Add player manually to queue',
                '!kick': 'Kick player from queue',
                '!close': 'Close opened queue',
                '!record-match': 'Record a win using [dire/radiant] [@10 mentions]',
                '!mmr': 'Set MMR for a queue',
                '!set-name/!rename': 'Rename player(careful)',
            },
        }

    def get_available_bot_commands(self):
        return {
            '!register': self.register_command,
            '!vouch': self.vouch_command,
            '!wh': self.whois_command,
            '!who': self.whois_command,
            '!whois': self.whois_command,
            '!profile': self.whois_command,
            '!ban': self.ban_command,
            '!unban': self.unban_command,
            '!stats': self.whois_command,
            '!q+': self.join_queue_command,
            '!q-': self.leave_queue_command,
            '!q': self.show_queues_command,
            '!join': self.join_queue_command,
            '!leave': self.leave_queue_command,
            '!list': self.show_queues_command,
            '!add': self.add_to_queue_command,
            '!kick': self.kick_from_queue_command,
            '!votekick': self.votekick_command,
            '!vk': self.votekick_command,
            '!mmr': self.mmr_command,
            '!top': self.top_command,
            '!bot': self.bottom_command,
            '!bottom': self.bottom_command,
            '!streak': self.streak_command,
            '!afk-ping': self.afk_ping_command,
            '!afkping': self.afk_ping_command,
            '!role': self.role_command,
            '!roles': self.role_command,
            '!recent': self.recent_matches_command,
            '!set-name': self.set_name_command,
            '!rename': self.set_name_command,
            '!set-mmr': self.set_mmr_command,
            '!adjust': self.set_mmr_command,
            '!set-dota-id': self.set_dota_id_command,
            '!record-match': self.record_match_command,
            '!help': self.help_command,
            '!close': self.close_queue_command,
            '!reg': self.attach_help_buttons_to_msg,
            '!r': self.attach_help_buttons_to_msg,
            '!jak': self.registration_help_command,
            '!info': self.registration_help_command,
        }

