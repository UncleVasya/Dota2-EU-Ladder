import re
from app.balancer.models import BalanceAnswer
from django.core.management.base import BaseCommand
from django.core.urlresolvers import reverse
from app.balancer.managers import BalanceResultManager, BalanceAnswerManager
from app.ladder.managers import MatchManager, PlayerManager
from enum import IntEnum
import gevent
from app.ladder.models import Player, LadderSettings
import dota2
import os

from steam import SteamClient, SteamID
from dota2 import Dota2Client

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

    def handle(self, *args, **options):
        bots_num = options['number']

        bot_login = os.environ.get('BOT_LOGIN', '')
        bot_password = os.environ.get('BOT_PASSWORD', '')
        credentials = [
            {
                'login': '%s%d' % (bot_login, i),
                'password': '%s%d' % (bot_password, i),
            } for i in xrange(1, bots_num+1)
        ]

        try:
            gevent.joinall([
                gevent.spawn(self.start_bot, c) for c in credentials
            ])
        finally:
            for bot in self.bots:
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
        dota.server = 'EU'
        dota.players = {}

        self.bots.append(dota)

        client.verbose_debug = True
        dota.verbose_debug = True

        @client.on('logged_on')
        def logged_on():
            dota.launch()

        @client.on('channel_secured')
        def send_login():
            if client.relogin_available:
                client.relogin()

        @client.on('reconnect')
        def handle_reconnect(delay):
            print 'Reconnect in %ds...' % delay

        @client.on('disconnected')
        def handle_disconnect():
            print 'Disconnected.'

            if client.relogin_available:
                print 'Reconnecting...'
                client.reconnect(maxdelay=30)

        @client.friends.on(SteamFriendlist.EVENT_FRIEND_INVITE)
        def friend_invite(user):
            client.friends.add(user.steam_id)

        @dota.on('ready')
        def dota_started():
            print 'Logged in: %s %s' % (dota.steam.username, dota.account_id)

            self.create_new_lobby(dota)

        @dota.on(dota2.features.Lobby.EVENT_LOBBY_NEW)
        def lobby_new(lobby):
            print '%s joined lobby %s' % (dota.steam.username, lobby.lobby_id)

            dota.join_practice_lobby_team()  # jump to unassigned players
            dota.join_lobby_chat()

        @dota.on(dota2.features.Lobby.EVENT_LOBBY_CHANGED)
        def lobby_changed(lobby):
            if int(lobby.state) == LobbyState.UI:
                # game isn't launched yet;
                # check if all players have right to play
                Command.kick_banned(dota)
                # Command.kick_blacklisted(dota)
                if dota.balance_answer:
                    Command.kick_unbalanced(dota)
                if dota.voice_required:
                    Command.kick_voice_issues(dota)
                if dota.min_mmr > 0:
                    Command.kick_low_mmr(dota)

            if int(lobby.state) == LobbyState.POSTGAME:
                # game ended, process result and create new lobby
                self.process_game_result(dota)
                self.create_new_lobby(dota)

        @dota.on(dota2.features.Chat.EVENT_CHAT_JOINED)
        def chat_joined(channel):
            print '%s joined chat channel %s' % (dota.steam.username, channel.channel_name)

        @dota.on(dota2.features.Chat.EVENT_CHAT_MESSAGE)
        def chat_message(channel, sender, text, msg_obj):
            if channel.channel_type != DOTAChatChannelType_t.DOTAChannelType_Lobby:
                return  # ignore postgame and other chats

            if text.startswith('!'):
                # look like this is bot command
                Command.bot_cmd(dota, msg_obj)

        client.login(credentials['login'], credentials['password'])
        client.run_forever()

    @staticmethod
    def create_new_lobby(bot):
        print 'Making new lobby\n'

        bot.balance_answer = None
        bot.staff_mode = False
        bot.players = {}
        bot.lobby_options = {
            'game_name': Command.generate_lobby_name(bot),
            'game_mode': dota2.enums.DOTA_GameMode.DOTA_GAMEMODE_CD,
            'server_region': GameServers[bot.server],
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
        text = msg.text
        command = text.split(' ')[0]

        commands = {
            '!balance': Command.balance_command,
            '!b': Command.balance_command,
            '!start': Command.start_command,
            '!mmr': Command.mmr_command,
            '!flip': bot.flip_lobby_teams,
            '!voice': Command.voice_command,
            '!teamkick': Command.teamkick_command,
            '!tk': Command.teamkick_command,
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
        }
        staff_only = ['!staff', '!forcestart', '!fs', '!swap', '!custom']

        # get player from DB using dota id
        try:
            player = Player.objects.get(dota_id=msg.account_id)
        except Player.DoesNotExist:
            bot.send_lobby_message('%s, who the fuck are you?' % msg.persona_name)
            return

        if player.banned:
            bot.send_lobby_message('%s, you are banned.' % msg.persona_name)
            return

        # check permissions when needed
        if not player.bot_access:
            # after balance only players and staff can use bot
            if bot.balance_answer:
                names = [p[0] for team in bot.balance_answer.teams
                         for p in team['players']]
                if player.name not in names:
                    bot.send_lobby_message('%s, this lobby is full. Join another one.' % msg.persona_name)
                    return

            # in staff mode only staff can use bot
            if bot.staff_mode:
                bot.send_lobby_message('%s, I am in staff-only mode.' % msg.persona_name)
                return

            # only staff can use this commands
            if command in staff_only:
                bot.send_lobby_message('%s, this command is staff-only.' % msg.persona_name)
                return

        # joke command for ulafzs
        # TODO: remove this eventually
        if command == '!ban' and player.name.lower() != 'ulafzs':
            bot.send_lobby_message('%s, only master ulafzs can use this command.' % msg.persona_name)
            return

        # user can use this command
        commands[command](bot, text)

    @staticmethod
    def balance_command(bot, command):
        print
        print 'Balancing players'

        # check if this is reset command
        try:
            if command.split(' ')[1] == 'off':
                bot.balance_answer = False
                bot.send_lobby_message('Balance cleared.')
                return
        except (IndexError, ValueError):
            pass

        # convert steam64 into 32bit dota id and build a dic of {id: player}
        players_steam = {
            SteamID(player.id).as_32: player for player in bot.lobby.members
            if player.team in (DOTA_GC_TEAM.GOOD_GUYS, DOTA_GC_TEAM.BAD_GUYS)
        }

        if len(players_steam) < 10:
            bot.send_lobby_message('We don\'t have 10 players')
            return

        # get players from DB using dota id
        players = Player.objects.filter(dota_id__in=players_steam.keys())
        players = {player.dota_id: player for player in players}

        unregistered = [players_steam[p].name for p in players_steam.keys()
                        if str(p) not in players]

        if unregistered:
            bot.send_lobby_message('I don\'t know these guys: %s' %
                                   ', '.join(unregistered))
            return

        print players

        players = [(p.name, p.ladder_mmr) for p in players.values()]
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
            bot.send_lobby_message('Team %d (avg. %d): %s' %
                                   (i+1, team['mmr'], ' | '.join(player_names)))
        bot.send_lobby_message(url)

    # TODO: get command from kwargs, so I don't have to add
    #       command argument for when I don't need it
    @staticmethod
    def start_command(bot, command):
        if not bot.balance_answer:
            bot.send_lobby_message('Please balance teams first.')
            return

        if not Command.check_teams_setup(bot):
            bot.send_lobby_message('Please join slots according to balance.')
            return

        bot.send_lobby_message('Ready to start')
        bot.launch_practice_lobby()

    @staticmethod
    def mmr_command(bot, command):
        print
        print 'Setting lobby MMR: '
        print command

        try:
            min_mmr = int(command.split(' ')[1])
            min_mmr = max(0, min(9000, min_mmr))
        except (IndexError, ValueError):
            return

        bot.min_mmr = min_mmr
        bot.lobby_options['game_name'] = Command.generate_lobby_name(bot)
        bot.config_practice_lobby(bot.lobby_options)

        bot.send_lobby_message('Min MMR set to %d' % min_mmr)

    @staticmethod
    def voice_command(bot, command):
        print
        print 'Voice command: '
        print command

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

        bot.send_lobby_message('Voice required set to %s' % bot.voice_required)

    @staticmethod
    def teamkick_command(bot, command):
        print
        print 'Teamkick command'
        print command

        try:
            name = command.split(' ')[1].lower()
        except (IndexError, ValueError):
            return

        for player in bot.lobby.members:
            if player.team not in (DOTA_GC_TEAM.GOOD_GUYS, DOTA_GC_TEAM.BAD_GUYS):
                continue
            if player.name.lower().startswith(name):
                print 'kicking %s' % player.name
                bot.practice_lobby_kick_from_team(SteamID(player.id).as_32)

    # this command checks if all lobby members are known to bot
    @staticmethod
    def check_command(bot, command):
        players_steam = {
            SteamID(player.id).as_32: player for player in bot.lobby.members
        }

        # get players from DB using dota id
        players = Player.objects.filter(
            dota_id__in=players_steam.keys()
        ).values_list('dota_id', flat=True)

        unregistered = [players_steam[p].name for p in players_steam.keys()
                        if str(p) not in players]

        if unregistered:
            bot.send_lobby_message('I don\'t know these guys: %s' %
                                   ', '.join(unregistered))
        else:
            bot.send_lobby_message('I know everybody here.')

    @staticmethod
    def forcestart_command(bot, command):
            Command.balance_answer = None
            bot.launch_practice_lobby()

    @staticmethod
    def mode_command(bot, command):
        print
        print 'Mode command'
        print command

        try:
            mode = command.split(' ')[1].upper()
        except (IndexError, ValueError):
            return

        bot.lobby_options['game_mode'] = GameModes[mode]
        bot.config_practice_lobby(bot.lobby_options)

        bot.send_lobby_message('Game mode set to %s' % mode)

    @staticmethod
    def server_command(bot, command):
        print
        print 'Server command'
        print command

        try:
            server = command.split(' ')[1].upper()
        except (IndexError, ValueError):
            return

        bot.server = server
        bot.lobby_options['server_region'] = GameServers[server]
        bot.config_practice_lobby(bot.lobby_options)

        bot.send_lobby_message('Game server set to %s' % server)

    @staticmethod
    def staff_command(bot, command):
        print
        print 'Staff command: '
        print command

        bot.staff_mode = True
        try:
            if command.split(' ')[1] == 'off':
                bot.staff_mode = False
        except (IndexError, ValueError):
            pass

        bot.send_lobby_message('Staff mode set to %s' % bot.staff_mode)

    @staticmethod
    def whois_command(bot, command):
        print
        print 'Whois command:'
        print command

        try:
            name = command.split(' ')[1].lower()
        except (IndexError, ValueError):
            return

        for member in bot.lobby.members:
            if member.name.lower().startswith(name):
                try:
                    player = Player.objects.get(dota_id=SteamID(member.id).as_32)
                except Player.DoesNotExist:
                    bot.send_lobby_message('%s: I don\'t know him' % member.name)
                    return

                match_count = player.matchplayer_set.filter(
                    match__season=LadderSettings.get_solo().current_season
                ).count()

                bot.send_lobby_message(
                    '%s: %s, Ladder MMR: %d, Score: %d, Games: %d' %
                    (member.name, player.name, player.ladder_mmr,
                     player.score, match_count)
                )
                return

        bot.send_lobby_message('No such name.')

    @staticmethod
    def teams_command(bot, command):
        print 'Teams command'

        if not bot.balance_answer:
            bot.send_lobby_message('Please balance teams first.')
            return

        teams = [
            Player.objects.filter(
                name__in=[player[0] for player in team['players']]
            ).order_by('-ladder_mmr')
            for team in bot.balance_answer.teams
        ]

        ladder_mmr = [' '.join(str(player.ladder_mmr) for player in team) for team in teams]

        [bot.send_lobby_message(' | '.join(player.name for player in team))
         for team in teams]
        bot.send_lobby_message('Ladder MMR:')
        [bot.send_lobby_message(team) for team in ladder_mmr]

    # swap 2 players in balance
    @staticmethod
    def swap_command(bot, command):
        print 'Swap command:'
        print command

        if not bot.balance_answer:
            bot.send_lobby_message('Please balance teams first.')
            return

        # get player indexes
        try:
            player_1 = int(command.split(' ')[1]) - 1
            player_2 = int(command.split(' ')[2]) - 1
            if not 0 <= player_1 < 5 or not 0 <= player_2 < 5:
                raise ValueError
        except (IndexError, ValueError):
            bot.send_lobby_message('Can\'t do that')
            return

        teams = [team['players'] for team in bot.balance_answer.teams]

        # swap players and generate new balance
        swap = teams[0][player_1]
        teams[0][player_1] = teams[1][player_2]
        teams[1][player_2] = swap

        bot.balance_answer = BalanceAnswerManager.balance_custom(teams)

        for i, team in enumerate(bot.balance_answer.teams):
            player_names = [p[0] for p in team['players']]
            bot.send_lobby_message(
                'Team %d (avg. %d): %s' %
                (i+1, team['mmr'], ' | '.join(player_names)))

    # creates balance record for already made-up teams
    # TODO: refactor this code to decrese repetition
    # TODO: between this func, balance_command() and check_teams()
    @staticmethod
    def custom_command(bot, command):
        print '!custom command'

        # convert steam64 into 32bit dota id and build a dic of {id: player}
        players_steam = {
            SteamID(player.id).as_32: player for player in bot.lobby.members
            if player.team in (DOTA_GC_TEAM.GOOD_GUYS, DOTA_GC_TEAM.BAD_GUYS)
        }

        if len(players_steam) < 10:
            bot.send_lobby_message('We don\'t have 10 players')
            return

        # get players from DB using dota id
        players = Player.objects.filter(dota_id__in=players_steam.keys())
        players = {player.dota_id: player for player in players}

        unregistered = [players_steam[p].name for p in players_steam.keys()
                        if str(p) not in players]

        if unregistered:
            bot.send_lobby_message('I don\'t know these guys: %s' %
                                   ', '.join(unregistered))
            return

        # create balance record for these players
        radiant = [(p.name, p.ladder_mmr) for key, p in players.iteritems()
                   if players_steam[int(key)].team == DOTA_GC_TEAM.GOOD_GUYS]
        dire = [(p.name, p.ladder_mmr) for key, p in players.iteritems()
                if players_steam[int(key)].team == DOTA_GC_TEAM.BAD_GUYS]

        bot.balance_answer = BalanceAnswerManager.balance_custom([radiant, dire])

        # TODO: create print_balance func
        # TODO and use it in here, balance_command(), teams_command(), swap_command()
        for i, team in enumerate(bot.balance_answer.teams):
            player_names = [p[0] for p in team['players']]
            bot.send_lobby_message(
                'Team %d (avg. %d): %s' %
                (i+1, team['mmr'], ' | '.join(player_names)))

    @staticmethod
    def ban_command(bot, command):
        print
        print 'Ban command:'
        print command

        try:
            name = command.split(' ')[1]
        except (IndexError, ValueError):
            return

        bot.send_lobby_message('Banning %s in...' % name)
        for i in range(5, 0, -1):
            gevent.sleep(1)
            bot.send_lobby_message('%d' % i)

        gevent.sleep(1)
        bot.send_lobby_message('JUST A PRANK!')

    @staticmethod
    def process_game_result(bot):
        print 'Game is finished!\n'
        print bot.lobby

        if not bot.balance_answer:
            print 'No balance exists (probably !forcestart)'
            return

        if bot.lobby.match_outcome == EMatchOutcome.RadVictory:
            print 'Radiant won!'
            MatchManager.record_balance(bot.balance_answer, 0)
        elif bot.lobby.match_outcome == EMatchOutcome.DireVictory:
            print 'Dire won!'
            MatchManager.record_balance(bot.balance_answer, 1)

    # checks if teams are setup according to balance
    @staticmethod
    def check_teams_setup(bot):
        print 'Checking teams setup\n'

        # get teams from game (player ids)
        # TODO: make function game_members_to_ids(lobby)
        game_teams = [set(), set()]
        for player in bot.lobby.members:
            if player.team in (DOTA_GC_TEAM.GOOD_GUYS, DOTA_GC_TEAM.BAD_GUYS):
                player_id = str(SteamID(player.id).as_32)  # TODO: models.Player.dota_id should be int, not str
                game_teams[player.team].add(player_id)

        print 'Game teams:'
        print game_teams

        # get teams from balance result (player ids)
        # TODO: make function balance_teams_to_ids(balance_answer)
        balancer_teams = [
            set(Player.objects.filter(name__in=[player[0] for player in team['players']])
                              .values_list('dota_id', flat=True))
            for team in bot.balance_answer.teams
        ]

        print 'Balancer teams:'
        print balancer_teams

        # compare teams from game to teams from balancer
        if game_teams == balancer_teams:
            print 'Teams are correct'
            return True
        elif game_teams == list(reversed(balancer_teams)):
            print 'Teams are correct (reversed)'

            # reverse teams in balance answer
            bot.balance_answer.teams = list(reversed(bot.balance_answer.teams))
            bot.balance_answer.save()

            print 'Corrected balance result:'
            print bot.balance_answer.teams

            return True

        print 'Teams don\'t match'

        # kick people from wrong slots
        for i, team in enumerate(game_teams):
            for player in team:
                if player not in balancer_teams[i]:
                    bot.practice_lobby_kick_from_team(int(player))

        return False

    @staticmethod
    def generate_lobby_name(bot):
        lobby_name = 'Ladder %s' %\
                     re.search('(\d+)$', bot.steam.username).group(0)

        if bot.min_mmr > 0:
            lobby_name += ' %d+' % bot.min_mmr
        if bot.voice_required:
            lobby_name += ' Voice'

        return lobby_name

    @staticmethod
    def kick_voice_issues(bot):
        players_steam = {
            SteamID(player.id).as_32: player for player in bot.lobby.members
            if player.team in (DOTA_GC_TEAM.GOOD_GUYS, DOTA_GC_TEAM.BAD_GUYS)
        }

        problematic = Player.objects.filter(
            dota_id__in=players_steam.keys(),
            voice_issues=True
        ).values_list('dota_id', flat=True)

        print 'Problematic: %s' % problematic

        for player in problematic:
            bot.practice_lobby_kick_from_team(int(player))

    @staticmethod
    def kick_low_mmr(bot):
        players_steam = {
            SteamID(player.id).as_32: player for player in bot.lobby.members
            if player.team in (DOTA_GC_TEAM.GOOD_GUYS, DOTA_GC_TEAM.BAD_GUYS)
        }
        players = Player.objects.filter(
            dota_id__in=players_steam.keys()
        ).values_list('dota_id', flat=True)

        if bot.min_mmr > 1000:
            # this is dota mmr
            problematic = players.filter(dota_mmr__lt=bot.min_mmr)
        else:
            # this is ladder mmr
            problematic = players.filter(ladder_mmr__lt=bot.min_mmr)

        print 'Problematic: %s' % problematic

        for player in problematic:
            bot.practice_lobby_kick_from_team(int(player))

    @staticmethod
    def kick_blacklisted(bot):
        old_players = bot.players
        current_players = {
            SteamID(player.id).as_32: player for player in bot.lobby.members
            if player.team in (DOTA_GC_TEAM.GOOD_GUYS, DOTA_GC_TEAM.BAD_GUYS)
        }

        bot.players = current_players

        print 'Old players: %s' % old_players
        print 'Current players: %s' % current_players

        if not old_players or not current_players:
            return

        joined_players = set(current_players.keys()) - set(old_players.keys())

        players = Player.objects.filter(
            dota_id__in=joined_players
        ).prefetch_related('blacklist', 'blacklisted_by')

        print 'New guys: %s' % players

        for p in players:
            print 'Player: %s' % p
            print 'Blacklist: %s' % p.blacklist.all()
            print 'Blacklisted by: %s' % p.blacklisted_by.all()

            blacklist = list(p.blacklist.all()) + list(p.blacklisted_by.all())
            blacklist = [int(b.dota_id) for b in blacklist]
            blacklist = set(blacklist)

            print 'Set: %s' % blacklist
            collision = blacklist.intersection(set(old_players.keys()))
            print 'Collision: %s' % collision

            if not collision:
                continue  # this guy can play

            # tell player he collides with other players
            collision = [old_players[c].name for c in collision]
            bot.send_lobby_message('%s, you can\'t play with: %s' %
                                   (p.name, ', '.join(collision)))

            bot.practice_lobby_kick_from_team(int(p.dota_id))

    @staticmethod
    def kick_banned(bot):
        players_steam = {
            SteamID(player.id).as_32: player for player in bot.lobby.members
            if player.team in (DOTA_GC_TEAM.GOOD_GUYS, DOTA_GC_TEAM.BAD_GUYS)
        }

        problematic = Player.objects.filter(
            dota_id__in=players_steam.keys(),
            banned=True
        ).values_list('dota_id', flat=True)

        for player in problematic:
            bot.send_lobby_message('%s, you are banned.' % players_steam[int(player)].name)
            bot.practice_lobby_kick_from_team(int(player))

    @staticmethod
    def kick_unbalanced(bot):
        players_steam = {
            SteamID(player.id).as_32: player for player in bot.lobby.members
            if player.team in (DOTA_GC_TEAM.GOOD_GUYS, DOTA_GC_TEAM.BAD_GUYS)
        }

        players_balance = [player for team in bot.balance_answer.teams
                           for player in team['players']]
        players_balance = [
            Player.objects.filter(name__in=[player[0] for player in players_balance])
                          .values_list('dota_id', flat=True)
        ]

        for player in players_steam.keys():
            if str(player) not in players_balance:
                bot.send_lobby_message('%s, this lobby is full. Join another one.' % players_steam[player].name)
                bot.practice_lobby_kick_from_team(player)
