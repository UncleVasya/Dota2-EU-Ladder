import re

from app.balancer.balancer import role_names
from app.balancer.models import BalanceAnswer
from django.core.management.base import BaseCommand
from django.core.cache import cache
from django.core.urlresolvers import reverse
from app.balancer.managers import BalanceResultManager, BalanceAnswerManager
from app.ladder.managers import MatchManager, PlayerManager
from django.utils.datetime_safe import datetime
from enum import IntEnum
import gevent
from app.ladder.models import Player, LadderSettings, LadderQueue
import dota2
import os

from steam.client import SteamClient, SteamID
from dota2.client import Dota2Client

from dota2.enums import DOTA_GC_TEAM, EMatchOutcome, DOTAChatChannelType_t
from steam.client.builtins.friends import SteamFriendlist


class LobbyState(IntEnum):
    UI = 0
    READYUP = 4
    SERVERSETUP = 1
    RUN = 2
    POSTGAME = 3
    NOTREADY = 5
    SERVERASSIGN = 6

GameModes = {
    'AP': dota2.enums.DOTA_GameMode.DOTA_GAMEMODE_AP,
    'AR': dota2.enums.DOTA_GameMode.DOTA_GAMEMODE_AR,
    'RD': dota2.enums.DOTA_GameMode.DOTA_GAMEMODE_RD,
    'SD': dota2.enums.DOTA_GameMode.DOTA_GAMEMODE_SD,
    'CD': dota2.enums.DOTA_GameMode.DOTA_GAMEMODE_CD,
    'CM': dota2.enums.DOTA_GameMode.DOTA_GAMEMODE_CM,
}

GameServers = {
    'EU': dota2.enums.EServerRegion.Europe,
    'USE': dota2.enums.EServerRegion.USEast,
    'USW': dota2.enums.EServerRegion.USWest,
    'AU': dota2.enums.EServerRegion.Australia,
    'SEA': dota2.enums.EServerRegion.Singapore,
}


# TODO: make DotaBot class

class Command(BaseCommand):
    def __init__(self):
        self.bots = []

    def add_arguments(self, parser):
        parser.add_argument('-n', '--number',
                            nargs='?', type=int, default=2, const=2)
        parser.add_argument('-f', '--first_bot',
                            nargs='?', type=int, default=1, const=1)

    def handle(self, *args, **options):
        bots_num = options['number']
        first_bot = options['first_bot']

        bot_login = os.environ.get('BOT_LOGIN', '')
        bot_password = os.environ.get('BOT_PASSWORD', '')
        credentials = [
            {
                'login': '%s%d' % (bot_login, i),
                'password': '%s%d' % (bot_password, i),
            } for i in range(first_bot, first_bot + bots_num)
        ]
        cache.set('bots', [c['login'] for c in credentials])

        try:
            gevent.joinall(
                [gevent.spawn(self.start_bot, c) for c in credentials] +
                [gevent.spawn(self.sync_queue)]
            )
        finally:
            cache.delete('bots')
            for bot in self.bots:
                bot.destroy_lobby()
                bot.exit()
                bot.steam.logout()

    def start_bot(self, credentials):
        client = SteamClient()
        dota = Dota2Client(client)

        dota.balance_answer = None
        dota.min_mmr = 0
        dota.lobby_options = {}
        dota.voice_required = False
        dota.staff_mode = False
        dota.game_start_time = None
        dota.server = 'EU'
        dota.players = {}  # TODO: this isn't used atm, make use of it
        dota.queue = None
        dota.use_queue = LadderSettings.get_solo().use_queue
        dota.player_draft = False

        self.bots.append(dota)

        client.verbose_debug = True
        dota.verbose_debug = True

        @client.on('logged_on')
        def logged_on():
            dota.launch()

        # TODO: don't try to relogin if we disconnected by KeyboardInterrupt
        @client.on('disconnected')
        def handle_disconnect():
            print('Disconnected: %s' % credentials['login'])

            delay = 30
            if client.relogin_available:
                print('Reconnecting...')
                client.reconnect(maxdelay=delay)
            else:
                print('Trying to login again in %d sec...' % delay)
                gevent.sleep(delay)

                client.login(credentials['login'], credentials['password'])
                client.run_forever()

        @client.on('channel_secured')
        def send_login():
            if client.relogin_available:
                client.relogin()

        @client.friends.on(SteamFriendlist.EVENT_FRIEND_INVITE)
        def friend_invite(user):
            client.friends.add(user.steam_id)

        @dota.on('ready')
        def dota_started():
            print('Logged in: %s %s' % (dota.steam.username, dota.account_id))

            # every second lobby is for high mmr players
            if self.bots.index(dota) % 2 == 0 and not dota.use_queue:
                Command.set_min_mmr(dota, 4500)

            # if lobby is hung up from previous session, leave it
            dota.destroy_lobby()
            self.create_new_lobby(dota)

        @dota.on(dota2.features.Lobby.EVENT_LOBBY_NEW)
        def lobby_new(lobby):
            print('%s joined lobby %s' % (dota.steam.username, lobby.lobby_id))

            dota.join_practice_lobby_team()  # jump to unassigned players
            dota.channels.join_lobby_channel()

        @dota.on(dota2.features.Lobby.EVENT_LOBBY_CHANGED)
        def lobby_changed(lobby):
            if int(lobby.state) == LobbyState.UI:
                # game isn't launched yet;
                # check if all players have right to play
                Command.kick_banned_from_lobby(dota)
                Command.kick_banned_from_playing(dota)
                # Command.kick_blacklisted(dota)
                if dota.use_queue:
                    Command.kick_not_in_queue(dota)
                else:
                    if dota.balance_answer:
                        Command.kick_unbalanced(dota)
                    if dota.voice_required:
                        Command.kick_voice_issues(dota)
                    if dota.min_mmr > 0:
                        Command.kick_low_mmr(dota)

            if int(lobby.state) == LobbyState.POSTGAME:
                # game ended, process result and create new lobby
                dota.game_start_time = None
                self.process_game_result(dota)
                self.create_new_lobby(dota)

            self.cache_lobby_status(dota)

        @dota.channels.on(dota2.features.chat.ChannelManager.EVENT_JOINED_CHANNEL)
        def chat_joined(channel):
            print('%s joined chat channel %s' % (dota.steam.username, channel.name))

        @dota.channels.on(dota2.features.chat.ChannelManager.EVENT_MESSAGE)
        def chat_message(channel, msg_obj):
            if channel.type != DOTAChatChannelType_t.DOTAChannelType_Lobby:
                return  # ignore postgame and other chats

            # strip whitespaces so bot can handle strings like " !register   Bob   4000"
            msg_obj.text = " ".join(msg_obj.text.split())
            if msg_obj.text.startswith('!'):
                # looks like this is bot command
                Command.bot_cmd(dota, msg_obj)

        client.login(credentials['login'], credentials['password'])
        client.run_forever()

    @staticmethod
    def create_new_lobby(bot):
        print('Making new lobby\n')

        bot.balance_answer = None
        bot.game_start_time = None
        bot.queue = None
        bot.player_draft = False
        bot.staff_mode = False
        bot.players = {}
        bot.invited_players = []
        bot.lobby_options = {
            'game_name': Command.generate_lobby_name(bot),
            'game_mode': dota2.enums.DOTA_GameMode.DOTA_GAMEMODE_CM,
            'server_region': GameServers[bot.server],
            'leagueid': int(os.environ.get('LEAGUE_ID', -1)),
            'fill_with_bots': False,
            'allow_spectating': True,
            'allow_cheats': False,
            'allchat': False,
            'dota_tv_delay': 0,  # TODO: this is LobbyDotaTV_10
            'pause_setting': 0,  # TODO: LobbyDotaPauseSetting_Unlimited
        }
        bot.create_practice_lobby(
            password=os.environ.get('LOBBY_PASSWORD', ''),
            options=bot.lobby_options)

    @staticmethod
    def bot_cmd(bot, msg):
        command = msg.text.split(' ')[0]

        commands = {
            '!balance': Command.balance_command,
            '!b': Command.balance_command,
            '!start': Command.start_command,
            '!mmr': Command.mmr_command,
            '!flip': bot.flip_lobby_teams,
            '!voice': Command.voice_command,
            '!teamkick': Command.teamkick_command,
            '!tk': Command.teamkick_command,
            '!lobbykick': Command.lobbykick_command,
            '!lk': Command.lobbykick_command,
            '!check': Command.check_command,
            '!forcestart': Command.forcestart_command,
            '!fs': Command.forcestart_command,
            '!mode': Command.mode_command,
            '!server': Command.server_command,
            '!staff': Command.staff_command,
            '!whois': Command.whois_command,
            '!wh': Command.whois_command,
            '!teams': Command.teams_command,
            '!swap': Command.swap_command,
            '!custom': Command.custom_command,
            '!ban': Command.ban_command,  # just a prank command atm
            '!new': Command.new_command,
            '!help': Command.help_command,
            '!commands': Command.help_command,
            '!register': Command.register_command,
            '!queue': Command.show_queue_command,
            '!q': Command.show_queue_command,
            '!playerdraft': Command.player_draft_command,
            '!pd': Command.player_draft_command,
            '!missing': Command.missing_command,
        }
        free_for_all = ['!register']
        staff_only = ['!staff', '!forcestart', '!fs', '!new', '!lobbykick', '!lk']

        disabled_by_queue = [
            '!register', '!b', '!balance', '!mmr', '!swap', '!voice',
        ]
        if bot.use_queue and command in disabled_by_queue:
            bot.channels.lobby.send('Bots are taken hostages by discord queue.')
            return

        # if command is free for all, no other checks required
        if command in free_for_all:
            commands[command](bot, msg)
            return

        # get player from DB using dota id
        try:
            player = Player.objects.get(dota_id=msg.account_id)
        except Player.DoesNotExist:
            bot.channels.lobby.send('%s, who the fuck are you?' % msg.persona_name)
            return

        if player.banned:
            bot.channels.lobby.send('%s, you are banned.' % msg.persona_name)
            return

        # check permissions when needed
        if not player.bot_access:
            # after balance only players and staff can use bot
            if bot.balance_answer:
                names = [p[0] for team in bot.balance_answer.teams
                         for p in team['players']]
                if player.name not in names:
                    bot.channels.lobby.send('%s, this lobby is full. Join another one.' % msg.persona_name)
                    return

            # in staff mode only staff can use bot
            if bot.staff_mode:
                bot.channels.lobby.send('%s, I am in staff-only mode.' % msg.persona_name)
                return

            # only staff can use this commands
            if command in staff_only:
                bot.channels.lobby.send('%s, this command is staff-only.' % msg.persona_name)
                return

        # user can use this command
        commands[command](bot, msg)

    @staticmethod
    def balance_command(bot, msg):
        command = msg.text
        print()
        print('Balancing players')

        # check if this is reset command
        try:
            if command.split(' ')[1] == 'off':
                bot.balance_answer = False
                bot.channels.lobby.send('Balance cleared.')
                return
        except (IndexError, ValueError):
            pass

        # convert steam64 into 32bit dota id and build a dic of {id: player}
        players_steam = {
            SteamID(player.id).as_32: player for player in bot.lobby.all_members
            if player.id and player.team in (DOTA_GC_TEAM.GOOD_GUYS, DOTA_GC_TEAM.BAD_GUYS)
        }

        if len(players_steam) < 10:
            bot.channels.lobby.send('We don\'t have 10 players')
            return

        # get players from DB using dota id
        players = Player.objects.filter(dota_id__in=players_steam.keys())
        players = {player.dota_id: player for player in players}

        unregistered = [players_steam[p].name for p in players_steam.keys()
                        if str(p) not in players]

        if unregistered:
            bot.channels.lobby.send('I don\'t know these guys: %s' %
                                   ', '.join(unregistered))
            return

        print(players)

        players = players.values()
        result = BalanceResultManager.balance_teams(players)

        try:
            answer_num = int(command.split(' ')[1])
            answer_num = max(1, min(40, answer_num))
        except (IndexError, ValueError):
            answer_num = 1

        url = reverse('balancer:balancer-result', args=(result.id,))
        host = os.environ.get('BASE_URL', 'localhost:8000')

        url = '%s%s?page=%s' % (host, url, answer_num)

        bot.balance_answer = answer = result.answers.all()[answer_num-1]
        for i, team in enumerate(answer.teams):
            player_names = [p[0] for p in team['players']]
            bot.channels.lobby.send('Team %d (avg. %d): %s' %
                                   (i+1, team['mmr'], ' | '.join(player_names)))
        bot.channels.lobby.send(url)

    # TODO: get command from kwargs, so I don't have to add
    #       command argument for when I don't need it
    @staticmethod
    def start_command(bot, msg):
        if not bot.balance_answer:
            if bot.player_draft:
                Command.custom_command(bot, None)
            else:
                bot.channels.lobby.send('Please balance teams first.')
                return

        if not Command.check_teams_setup(bot):
            bot.channels.lobby.send('Please join slots according to balance.')
            return

        bot.channels.lobby.send('Ready to start')
        Command.start_game(bot)

    @staticmethod
    def mmr_command(bot, msg):
        command = msg.text
        print()
        print('Setting lobby MMR: ')
        print(command)

        try:
            min_mmr = int(command.split(' ')[1])
            min_mmr = max(0, min(9000, min_mmr))
        except (IndexError, ValueError):
            return

        Command.set_min_mmr(bot, min_mmr)
        bot.channels.lobby.send(f'Min MMR set to {min_mmr}')

    @staticmethod
    def voice_command(bot, msg):
        command = msg.text
        print()
        print('Voice command: ')
        print(command)

        bot.voice_required = True
        try:
            if command.split(' ')[1] == 'off':
                bot.voice_required = False
        except (IndexError, ValueError):
            pass

        if bot.voice_required:
            Command.kick_voice_issues(bot)

        bot.lobby_options['game_name'] = Command.generate_lobby_name(bot)
        bot.config_practice_lobby(bot.lobby_options)

        bot.channels.lobby.send('Voice required set to %s' % bot.voice_required)

    @staticmethod
    def teamkick_command(bot, msg):
        command = msg.text
        print()
        print('Teamkick command')
        print(command)

        try:
            name = command.split(None, 1)[1].lower()
        except (IndexError, ValueError):
            return

        for player in bot.lobby.all_members:
            if player.team not in (DOTA_GC_TEAM.GOOD_GUYS, DOTA_GC_TEAM.BAD_GUYS):
                continue
            if player.name.lower().startswith(name):
                print('kicking %s' % player.name)
                bot.practice_lobby_kick_from_team(SteamID(player.id).as_32)

    @staticmethod
    def lobbykick_command(bot, msg):
        """
        Sets a lobby auto-kick for a specific player (based on his dota_id)

        :param bot: bot that executes current command (Dota2Client object)
        :param msg: a message from dota server (dictionary). We are interested in msg.text - this is a command text.
                    msg.text can be in 2 formats:
                        - enable auto-kick:  !lobbykick DOTA_ID
                        - disable auto-kick: !lobbykick off DOTA_ID
        """
        command = msg.text
        print()
        print('Lobbykick command')
        print(command)

        try:
            dota_id = int(command.split()[-1])
            off = (command.split()[1] == 'off')
        except (IndexError, ValueError):
            bot.channels.lobby.send('Can\'t do that')
            return

        player = None
        try:
            player = Player.objects.get(dota_id=dota_id)
            player.banned = None if off else Player.BAN_PLAYING_AND_LOBBY
            player.save()
        except Player.DoesNotExist:
            # this player isn't in DB, so we create a dummy account with 'banned' flag for him
            if not off:
                Player.objects.create(
                    name='Banned_%s' % dota_id,
                    dota_mmr=0,
                    dota_id=str(dota_id),
                    banned=Player.BAN_PLAYING_AND_LOBBY,
                )
        if not off:
            bot.practice_lobby_kick(dota_id)

        bot.channels.lobby.send('%s auto-kick for %s' %
                                ('DISABLED' if off else 'ENABLED', player.name if player else dota_id))

    # this command checks if all lobby members are known to bot
    @staticmethod
    def check_command(bot, msg):
        players_steam = {
            SteamID(player.id).as_32: player for player in bot.lobby.all_members
            if player.id and SteamID(player.id).as_32 != bot.account_id
        }

        # get players from DB using dota id
        players = Player.objects.filter(
            dota_id__in=players_steam.keys()
        ).values_list('dota_id', flat=True)

        unregistered = [players_steam[p].name for p in players_steam.keys()
                        if str(p) not in players]

        if unregistered:
            bot.channels.lobby.send('I don\'t know these guys: %s' %
                                   ', '.join(unregistered))
        else:
            bot.channels.lobby.send('I know everybody here.')

    @staticmethod
    def forcestart_command(bot, msg):
        bot.balance_answer = None
        Command.start_game(bot)

    @staticmethod
    def mode_command(bot, msg):
        command = msg.text
        print()
        print('Mode command')
        print(command)

        try:
            mode = command.split(' ')[1].upper()
        except (IndexError, ValueError):
            return

        bot.lobby_options['game_mode'] = GameModes[mode]
        bot.config_practice_lobby(bot.lobby_options)

        bot.channels.lobby.send('Game mode set to %s' % mode)

    @staticmethod
    def server_command(bot, msg):
        command = msg.text
        print()
        print('Server command')
        print(command)

        try:
            server = command.split(' ')[1].upper()
        except (IndexError, ValueError):
            return

        bot.server = server
        bot.lobby_options['server_region'] = GameServers[server]
        bot.config_practice_lobby(bot.lobby_options)

        bot.channels.lobby.send('Game server set to %s' % server)

    @staticmethod
    def staff_command(bot, msg):
        command = msg.text
        print()
        print('Staff command: ')
        print(command)

        bot.staff_mode = True
        try:
            if command.split(' ')[1] == 'off':
                bot.staff_mode = False
        except (IndexError, ValueError):
            pass

        bot.channels.lobby.send('Staff mode set to %s' % bot.staff_mode)

    @staticmethod
    def whois_command(bot, msg):
        command = msg.text
        print()
        print('Whois command:')
        print(command)

        try:
            name = command.split(None, 1)[1].lower()
        except (IndexError, ValueError):
            return

        # first search with 'startswith' and if no one found repeat with 'contains'
        member = next((m for m in bot.lobby.all_members if m.name.lower().startswith(name)), None)
        if not member:
            member = next((m for m in bot.lobby.all_members if name in m.name.lower()), None)

        if not member:
            bot.channels.lobby.send('No such name.')
            return

        try:
            player = Player.objects.get(dota_id=SteamID(member.id).as_32)
        except Player.DoesNotExist:
            bot.channels.lobby.send(f'{member.name}: I don\'t know him')
            return

        match_count = player.matchplayer_set.filter(
            match__season=LadderSettings.get_solo().current_season
        ).count()

        roles = ' '.join(str(getattr(player.roles, r)) for r in role_names)
        bot.channels.lobby.send(
            f'{member.name}: {player.name}, MMR: {player.ladder_mmr}, '
            f'Games: {match_count}, Roles: {roles}'
        )

    @staticmethod
    def teams_command(bot, msg):
        print('Teams command')

        balance = bot.balance_answer
        if not bot.balance_answer:
            bot.channels.lobby.send('Please balance teams first.')
            return

        team_str = []
        for i, team in enumerate(balance.teams):
            if team['role_score_sum']:
                # this is balance with roles
                player_names = [f'{i+1}. {p[0]}' for i, p in enumerate(team['players'])]
            else:
                # balance without roles
                player_names = [p[0] for p in team['players']]
            team_str.append(f'Team {i+1}: ' + ' | '.join(player_names))

        ladder_mmr = [' '.join(str(p[1]) for p in team['players']) for team in balance.teams]

        [bot.channels.lobby.send(team) for team in team_str]
        bot.channels.lobby.send('Ladder MMR:')
        [bot.channels.lobby.send(team) for team in ladder_mmr]

    # swap 2 players in balance
    @staticmethod
    def swap_command(bot, msg):
        command = msg.text
        print('Swap command:')
        print(command)

        if not bot.balance_answer:
            bot.channels.lobby.send('Please balance teams first.')
            return

        # get player indexes
        try:
            player_1 = int(command.split(' ')[1]) - 1
            player_2 = int(command.split(' ')[2]) - 1
            if not 0 <= player_1 < 5 or not 0 <= player_2 < 5:
                raise ValueError
        except (IndexError, ValueError):
            bot.channels.lobby.send('Can\'t do that')
            return

        teams = [team['players'] for team in bot.balance_answer.teams]

        # swap players and generate new balance
        swap = teams[0][player_1]
        teams[0][player_1] = teams[1][player_2]
        teams[1][player_2] = swap

        bot.balance_answer = BalanceAnswerManager.balance_custom(teams)

        for i, team in enumerate(bot.balance_answer.teams):
            player_names = [p[0] for p in team['players']]
            bot.channels.lobby.send(
                'Team %d (avg. %d): %s' %
                (i+1, team['mmr'], ' | '.join(player_names)))

    # creates balance record for already made-up teams
    # TODO: refactor this code to decrese repetition
    # TODO: between this func, balance_command() and check_teams()
    @staticmethod
    def custom_command(bot, msg):
        print('!custom command')

        # convert steam64 into 32bit dota id and build a dic of {id: player}
        players_steam = {
            SteamID(player.id).as_32: player for player in bot.lobby.all_members
            if player.id and player.team in (DOTA_GC_TEAM.GOOD_GUYS, DOTA_GC_TEAM.BAD_GUYS)
        }

        if len(players_steam) < 10:
            bot.channels.lobby.send('We don\'t have 10 players')
            return

        # get players from DB using dota id
        players = Player.objects.filter(dota_id__in=players_steam.keys())
        players = {player.dota_id: player for player in players}

        unregistered = [players_steam[p].name for p in players_steam.keys()
                        if str(p) not in players]

        if unregistered:
            bot.channels.lobby.send('I don\'t know these guys: %s' %
                                   ', '.join(unregistered))
            return

        # create balance record for these players
        radiant = [(p.name, p.ladder_mmr) for key, p in players.items()
                   if players_steam[int(key)].team == DOTA_GC_TEAM.GOOD_GUYS]
        dire = [(p.name, p.ladder_mmr) for key, p in players.items()
                if players_steam[int(key)].team == DOTA_GC_TEAM.BAD_GUYS]

        bot.balance_answer = BalanceAnswerManager.balance_custom([radiant, dire])

        # TODO: create print_balance func
        # TODO and use it in here, balance_command(), teams_command(), swap_command()
        for i, team in enumerate(bot.balance_answer.teams):
            player_names = [p[0] for p in team['players']]
            bot.channels.lobby.send(
                'Team %d (avg. %d): %s' %
                (i+1, team['mmr'], ' | '.join(player_names)))

    @staticmethod
    def ban_command(bot, msg):
        command = msg.text
        print()
        print('Ban command:')
        print(command)

        try:
            name = command.split(None, 1)[1]
        except (IndexError, ValueError):
            return

        bot.channels.lobby.send('Banning %s in...' % name)
        for i in range(5, 0, -1):
            gevent.sleep(1)
            bot.channels.lobby.send('%d' % i)

        gevent.sleep(1)
        bot.channels.lobby.send('JUST A PRANK!')

    @staticmethod
    def new_command(bot, msg):
        print()
        print('!new command')

        bot.channels.lobby.send('Creating new lobby.')
        bot.destroy_lobby()
        gevent.sleep(5)
        Command.create_new_lobby(bot)

    @staticmethod
    def help_command(bot, msg):
        bot.channels.lobby.send(
            'Documentation is coming. '
            'It\'s not coming in your lifetime, but it\'s coming.')

    @staticmethod
    def register_command(bot, msg):
        command = msg.text
        print()
        print('!register command')
        print(command)

        min_allowed_mmr = 1000
        max_allowed_mmr = 7000

        try:
            params = command.split(None, 1)[1]
            name = params.rsplit(None, 1)[0]
            mmr = int(params.rsplit(None, 1)[1])
        except (IndexError, ValueError):
            bot.channels.lobby.send('Wrong command usage. Example: !register Uvs 3000')
            return

        # check if we can register this player
        if Player.objects.filter(dota_id=msg.account_id).exists():
            bot.channels.lobby.send('Already registered, bro.')
            return

        if Player.objects.filter(name=name).exists():
            bot.channels.lobby.send('This name is already taken. Try another or talk to admins.')
            return

        if mmr < min_allowed_mmr:
            bot.channels.lobby.send('Your dick is too small. Ask admins to register you.')
            return

        if mmr > max_allowed_mmr:
            bot.channels.lobby.send('Your dick is too big. Show it to admins!')
            return

        # all is good, can register
        Player.objects.create(
            name=name,
            dota_mmr=mmr,
            dota_id=msg.account_id,
        )
        Player.objects.update_ranks()

        bot.channels.lobby.send('Welcome to ladder, %s! You can play now.' % name)

    @staticmethod
    def show_queue_command(bot, msg):
        print('\n!queue command.')

        q = bot.queue
        if not q:
            bot.channels.lobby.send('No queue assigned to this bot.')
            return

        bot.channels.lobby.send(f'Queue #{q.id}')
        bot.channels.lobby.send(f'Min MMR: {q.min_mmr}')
        bot.channels.lobby.send(
            f'Players: {q.players.count()} (' +
            f' | '.join(p.name for p in q.players.all()) + ')'
        )

    @staticmethod
    def player_draft_command(bot, msg):
        print('\n!playerdraft command.')

        bot.player_draft = not bot.player_draft
        if bot.player_draft:
            bot.balance_answer = None
            bot.channels.lobby.send(
                f'Player draft is turned ON. 2 highest MMR players please draft.')
        else:
            bot.channels.lobby.send(
                f'Player draft is turned OFF. Use !teams to see auto-balance.')

    @staticmethod
    def missing_command(bot, msg):
        print('\n!missing command.')

        if not bot.queue:
            bot.channels.lobby.send('This lobby is not currently in a queue.')
            return

        players_lobby = [SteamID(p.id).as_32 for p in bot.lobby.all_members]
        missing = bot.queue.players\
            .exclude(dota_id__in=players_lobby)\
            .values_list('name', flat=True)

        bot.channels.lobby.send('Missing players: ' + ' | '.join(missing))

    @staticmethod
    def process_game_result(bot):
        print('Game is finished!\n')
        print(bot.lobby)

        if not bot.balance_answer:
            print('No balance exists (probably !forcestart)')
            return

        # TODO: write smth like "record_balance(answer, 0 if RadVictory else 1, match_id)"
        if bot.lobby.match_outcome == EMatchOutcome.RadVictory:
            print('Radiant won!')
            MatchManager.record_balance(bot.balance_answer, 0, bot.lobby.match_id)
        elif bot.lobby.match_outcome == EMatchOutcome.DireVictory:
            print('Dire won!')
            MatchManager.record_balance(bot.balance_answer, 1, bot.lobby.match_id)

    # checks if teams are setup according to balance
    @staticmethod
    def check_teams_setup(bot):
        print('Checking teams setup\n')

        # get teams from game (player ids)
        # TODO: make function game_members_to_ids(lobby)
        game_teams = [set(), set()]
        for player in bot.lobby.all_members:
            if player.team in (DOTA_GC_TEAM.GOOD_GUYS, DOTA_GC_TEAM.BAD_GUYS):
                player_id = str(SteamID(player.id).as_32)  # TODO: models.Player.dota_id should be int, not str
                game_teams[player.team].add(player_id)

        print('Game teams:')
        print(game_teams)

        # get teams from balance result (player ids)
        # TODO: make function balance_teams_to_ids(balance_answer)
        balancer_teams = [
            set(Player.objects.filter(name__in=[player[0] for player in team['players']])
                              .values_list('dota_id', flat=True))
            for team in bot.balance_answer.teams
        ]

        print('Balancer teams:')
        print(balancer_teams)

        # compare teams from game to teams from balancer
        if game_teams == balancer_teams:
            print('Teams are correct')
            return True
        elif game_teams == list(reversed(balancer_teams)):
            print('Teams are correct (reversed)')

            # reverse teams in balance answer
            bot.balance_answer.teams = list(reversed(bot.balance_answer.teams))
            bot.balance_answer.save()

            print('Corrected balance result:')
            print(bot.balance_answer.teams)

            return True

        print('Teams don\'t match')

        # kick people from wrong slots
        for i, team in enumerate(game_teams):
            for player in team:
                if player not in balancer_teams[i]:
                    bot.practice_lobby_kick_from_team(int(player))

        return False

    @staticmethod
    def generate_lobby_name(bot):
        # TODO: get lobby name from env
        lobby_name = 'RD2L Ladder %s' %\
                     re.search('(\d+)$', bot.steam.username).group(0)

        if bot.min_mmr > 0:
            lobby_name += ' %d+' % bot.min_mmr
        if bot.voice_required:
            lobby_name += ' Voice'

        return lobby_name

    @staticmethod
    def generate_lobby_queue_name(bot):
        if not bot.queue:
            return Command.generate_lobby_name(bot)

        lobby_name = f'RD2L Queue #{bot.queue.id}'

        if bot.queue.min_mmr > 0:
            lobby_name += f' {bot.queue.min_mmr}+'

        return lobby_name

    @staticmethod
    def kick_voice_issues(bot):
        players_steam = {
            SteamID(player.id).as_32: player for player in bot.lobby.all_members
            if player.id and player.team in (DOTA_GC_TEAM.GOOD_GUYS, DOTA_GC_TEAM.BAD_GUYS)
        }

        problematic = Player.objects.filter(
            dota_id__in=players_steam.keys(),
            voice_issues=True
        ).values_list('dota_id', flat=True)

        print('Problematic: %s' % problematic)

        for player in problematic:
            bot.practice_lobby_kick_from_team(int(player))

    @staticmethod
    def kick_low_mmr(bot):
        players_steam = {
            SteamID(player.id).as_32: player for player in bot.lobby.all_members
            if player.id and player.team in (DOTA_GC_TEAM.GOOD_GUYS, DOTA_GC_TEAM.BAD_GUYS)
        }
        players = Player.objects.filter(
            dota_id__in=list(players_steam.keys())
        ).values_list('dota_id', flat=True)

        problematic = players.filter(ladder_mmr__lt=bot.min_mmr)
        print('Problematic: %s' % problematic)

        for player in problematic:
            bot.practice_lobby_kick_from_team(int(player))

    @staticmethod
    def kick_blacklisted(bot):
        old_players = bot.players
        current_players = {
            SteamID(player.id).as_32: player for player in bot.lobby.all_members
            if player.team in (DOTA_GC_TEAM.GOOD_GUYS, DOTA_GC_TEAM.BAD_GUYS)
        }

        bot.players = current_players

        print('Old players: %s' % old_players)
        print('Current players: %s' % current_players)

        if not old_players or not current_players:
            return

        joined_players = set(current_players.keys()) - set(old_players.keys())

        players = Player.objects.filter(
            dota_id__in=joined_players
        ).prefetch_related('blacklist', 'blacklisted_by')

        print('New guys: %s' % players)

        for p in players:
            print('Player: %s' % p)
            print('Blacklist: %s' % p.blacklist.all())
            print('Blacklisted by: %s' % p.blacklisted_by.all())

            blacklist = list(p.blacklist.all()) + list(p.blacklisted_by.all())
            blacklist = [int(b.dota_id) for b in blacklist]
            blacklist = set(blacklist)

            print('Set: %s' % blacklist)
            collision = blacklist.intersection(set(old_players.keys()))
            print('Collision: %s' % collision)

            if not collision:
                continue  # this guy can play

            # tell player he collides with other players
            collision = [old_players[c].name for c in collision]
            bot.channels.lobby.send('%s, you can\'t play with: %s' %
                                   (p.name, ', '.join(collision)))

            bot.practice_lobby_kick_from_team(int(p.dota_id))

    @staticmethod
    def kick_banned_from_playing(bot):
        players_steam = {
            SteamID(player.id).as_32: player for player in bot.lobby.all_members
            if player.id and player.team in (DOTA_GC_TEAM.GOOD_GUYS, DOTA_GC_TEAM.BAD_GUYS)
        }

        problematic = Player.objects.filter(
            dota_id__in=players_steam.keys(),
            banned=Player.BAN_PLAYING
        ).values_list('dota_id', flat=True)

        for player in problematic:
            bot.channels.lobby.send('%s, you are banned from playing.' % players_steam[int(player)].name)
            bot.practice_lobby_kick_from_team(int(player))

    @staticmethod
    def kick_banned_from_lobby(bot):
        players_steam = {
            SteamID(player.id).as_32: player for player in bot.lobby.all_members
            if player.id
        }

        problematic = Player.objects.filter(
            dota_id__in=players_steam.keys(),
            banned=Player.BAN_PLAYING_AND_LOBBY
        ).values_list('dota_id', flat=True)

        for player in problematic:
            bot.practice_lobby_kick(int(player))

    @staticmethod
    def kick_unbalanced(bot):
        players_steam = {
            SteamID(player.id).as_32: player for player in bot.lobby.all_members
            if player.team in (DOTA_GC_TEAM.GOOD_GUYS, DOTA_GC_TEAM.BAD_GUYS)
        }

        players_balance = [player for team in bot.balance_answer.teams
                           for player in team['players']]
        players_balance = Player.objects\
            .filter(name__in=[player[0] for player in players_balance])\
            .values_list('dota_id', flat=True)

        for player in players_steam.keys():
            if str(player) not in players_balance:
                bot.channels.lobby.send('%s, this lobby is full. Join another one.' % players_steam[player].name)
                bot.practice_lobby_kick_from_team(player)

    @staticmethod
    def kick_not_in_queue(bot):
        players_steam = {
            SteamID(player.id).as_32: player for player in bot.lobby.all_members
            if player.id and player.team in (DOTA_GC_TEAM.GOOD_GUYS, DOTA_GC_TEAM.BAD_GUYS)
        }

        players_queue = []
        if bot.queue:
            players_queue = bot.queue.players.all()\
                .values_list('dota_id', flat=True)

        for player in players_steam.keys():
            if str(player) not in players_queue:
                bot.channels.lobby.send(
                    f'{players_steam[player].name}, You are not in this queue.')
                bot.practice_lobby_kick_from_team(player)

    @staticmethod
    def set_min_mmr(bot, mmr):
        bot.min_mmr = mmr
        bot.lobby_options['game_name'] = Command.generate_lobby_name(bot)
        bot.config_practice_lobby(bot.lobby_options)

    @staticmethod
    def cache_lobby_status(bot):
        def cache_member(member):
            return {'dota_id': SteamID(member.id).as_32,
                    'name': member.name}

        lobby = bot.lobby

        # filter out bot account
        members = [player for player in lobby.all_members if SteamID(player.id).as_32 != bot.account_id]

        players = [player for player in members
                   if player.team in (DOTA_GC_TEAM.GOOD_GUYS, DOTA_GC_TEAM.BAD_GUYS)]
        # 2 teams with 5 slots each, None for empty slot
        teams = [[None]*5 for _ in range(2)]
        for player in players:
            teams[player.team][player.slot-1] = cache_member(player)

        unassigned = [cache_member(member) for member in members
                      if member.team not in (DOTA_GC_TEAM.GOOD_GUYS, DOTA_GC_TEAM.BAD_GUYS)]

        lobby = {
            'game_name': lobby.game_name,
            'state': 'ready' if int(lobby.state) == LobbyState.UI else 'game',
            'game_start_time': bot.game_start_time,
            'members': [SteamID(member.id).as_32 for member in members],
            'teams': teams,
            'unassigned': unassigned,
            'lobby_str': str(lobby)
        }
        cache.set(bot.steam.username, lobby)

    @staticmethod
    def start_game(bot):
        bot.game_start_time = datetime.now()
        if bot.queue:
            bot.queue.active = False
            bot.queue.save()
        bot.launch_practice_lobby()

    @staticmethod
    def invite_players(bot):
        # invite queue players into lobby
        players = [p for p in bot.queue.players.all()
                   if p not in bot.invited_players]

        for player in players:
            steam_id = SteamID(player.dota_id)
            bot.invite_to_lobby(steam_id)
            bot.invited_players.append(player)

        # print(f'invited players: {bot.invited_players}')

    @staticmethod
    def assign_queue_to_bot(bot, queue):
        # if in game, do nothing
        if bot.game_start_time:
            return

        bot.queue = queue
        if not bot.player_draft:
            bot.balance_answer = queue.balance

        # remove un-queued players from invited_players list;
        # this is done so if they re-join, they will get invite again
        if queue:
            bot.invited_players = [p for p in bot.invited_players
                                   if p in bot.queue.players.all()]

        # if queue is full, invite players
        if queue and queue.players.count() == 10:
            Command.invite_players(bot)

        bot.lobby_options['game_name'] = Command.generate_lobby_queue_name(bot)
        bot.config_practice_lobby(bot.lobby_options)

    def sync_queue(self):
        def is_bot_free(bot):
            # 1) not in game, 2) lobby ready, 3) no queue assigned
            return not bot.game_start_time and bot.lobby and not bot.queue

        while True:
            gevent.sleep(5)
            # print('===========sync_queue=============')

            if not all(bot.ready for bot in self.bots):
                print('Bots not ready')
                continue

            queues = LadderQueue.objects.filter(active=True)
            queues = {q.id: q for q in queues}

            # print('Active queues: ' + ' | '.join(str(q) for q in queues.values()))

            # first update assigned queues in bots
            # at this step some of the bots might turn free
            busy_bots = [b for b in self.bots if b.queue and b.lobby]

            # print('Busy bots:\n' +
            #       '\n'.join(f'{b.lobby.game_name}: {b.queue}' for b in busy_bots))

            for bot in busy_bots:
                q = queues.pop(bot.queue.id, None)
                if q:
                    # update queue object in bot
                    Command.assign_queue_to_bot(bot, q)
                elif not bot.game_start_time:
                    # bot became free
                    bot.destroy_lobby()
                    gevent.sleep(5)
                    Command.create_new_lobby(bot)

            # now assign new queues to free bots
            free_bots = [b for b in self.bots if is_bot_free(b)]

            # print('Free bots: ' + ' | '.join(b.lobby.game_name for b in free_bots))

            for bot in free_bots:
                if len(queues) > 0:
                    _, q = queues.popitem()
                    Command.assign_queue_to_bot(bot, q)
