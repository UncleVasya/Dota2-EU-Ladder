from django.core.management.base import BaseCommand
from django.core.urlresolvers import reverse
from app.balancer.managers import BalanceResultManager
from app.balancer.models import BalanceAnswer
from app.ladder.managers import MatchManager
from enum import IntEnum
import gevent
from app.ladder.models import Player
import dota2
import os

from steam import SteamClient, SteamID
from dota2 import Dota2Client

from dota2.enums import DOTA_GC_TEAM, EMatchOutcome, DOTAChatChannelType_t


class LobbyState(IntEnum):
    UI = 0
    READYUP = 4
    SERVERSETUP = 1
    RUN = 2
    POSTGAME = 3
    NOTREADY = 5
    SERVERASSIGN = 6


class Command(BaseCommand):
    def __init__(self):
        self.bots = []

    def add_arguments(self, parser):
        parser.add_argument('-n', '--number',
                            nargs='?', type=int, default=1, const=1)

    def handle(self, *args, **options):
        bots_num = options['number']

        credentials = [
            {
                'login': 'login%d' % i,
                'password': 'password%d' % i,
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

        self.bots.append(dota)

        client.verbose_debug = True
        dota.verbose_debug = True

        @client.on('logged_on')
        def start_dota():
            dota.launch()

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

            # process known commands
            if text.startswith('!balance'):
                self.balance_command(dota)
            elif text.startswith('!start'):
                self.start_command(dota)

            # process test commands
            elif text.startswith('!dummy_balance'):
                dota.balance_answer = BalanceAnswer(
                    teams=[
                        {'players': [('Uvs', 3000)]},
                        {'players': []},
                    ]
                )

            else:
                dota.send_lobby_message('Fuck off, %s!' % sender)

        client.login(credentials['login'], credentials['password'])
        client.run_forever()

    def create_new_lobby(self, bot):
        print 'Making new lobby\n'

        bot.balance_answer = None

        bot.create_practice_lobby(password='eu', options={
            'game_name': 'Inhouse Ladder',
            'game_mode': dota2.enums.DOTA_GameMode.DOTA_GAMEMODE_CD,
            'server_region': int(dota2.enums.EServerRegion.Europe),
            'fill_with_bots': True,
            'allow_spectating': True,
            'allow_cheats': False,
            'allchat': True,
            'dota_tv_delay': 2,
            'pause_setting': 1,
        })

    def balance_command(self, bot):
        print
        print 'Balancing players'

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

        url = reverse('balancer:balancer-result', args=(result.id,))
        url = os.environ.get('BASE_URL', 'localhost:8000') + url

        bot.balance_answer = answer = result.answers.first()
        for i, team in enumerate(answer.teams):
            player_names = [p[0] for p in team['players']]
            bot.send_lobby_message('Team %d: %s' % (i+1, ' | '.join(player_names)))
        bot.send_lobby_message(url)

    def start_command(self, bot):
        if not bot.balance_answer:
            bot.send_lobby_message('Please balance teams first.')
            return

        if not self.check_teams_setup(bot):
            bot.send_lobby_message('Please join slots according to balance.')
            return

        bot.send_lobby_message('Ready to start')
        bot.launch_practice_lobby()

    def process_game_result(self, bot):
        print 'Game is finished!\n'
        print bot.lobby

        # create dummy balance result
        players = Player.objects.filter(rank__gt=0)[:10]
        players = [(p.name, p.ladder_mmr) for p in players]

        result = BalanceResultManager.balance_teams(players)
        bot.balance_answer = result.answers.first()

        if bot.lobby.match_outcome == EMatchOutcome.RadVictory:
            print 'Radiant won!'
            MatchManager.record_balance(bot.balance_answer, 0)
        elif bot.lobby.match_outcome == EMatchOutcome.DireVictory:
            print 'Dire won!'
            MatchManager.record_balance(bot.balance_answer, 1)

    # checks if teams setup according to balance
    def check_teams_setup(self, bot):
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
            # bot.balace_answer.save()

            print 'Corrected balance result:'
            print bot.balance_answer.teams

            return True

        print 'Teams don\'t match'

        return False


