import discord
from django.core.management.base import BaseCommand
import os

from django.db.models import Q

from app.ladder.managers import PlayerManager
from app.ladder.models import Player, LadderSettings


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
        #     '!q+': Command.join_queue,
        #     '!q-': Command.leave_queue,
        #     '!q': Command.show_queue,
        #     '!add': Command.add_to_queue,
        #     '!kick': Command.kick_from_queue,
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
        await commands[command](msg)

    async def register_command(self, msg):
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
                """Wrong command usage. 
                Format: "!register %username% %mmr% %dota_id%". Example: 
                    !register Uvs 3000 444510529"""
            )
            return

        # check if we can register this player
        if Player.objects.filter(Q(discord_id=msg.author.id) | Q(dota_id=dota_id)).exists():
            await msg.channel.send('Already registered, bro.')
            return

        if Player.objects.filter(name__iexact=name).exists():
            await msg.channel.send('This name is already taken. Try another or talk to admins.')
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

    async def vouch_command(self, msg):
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
        await msg.channel.send(f'{player_discord.mention} has been vouched. He can play now!')

    async def whois_command(self, msg):
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
