from django.core.management.base import BaseCommand
from django.core.urlresolvers import reverse
from django.db import transaction
import gevent
from app.balancer import models
from app.balancer.balancer import balance_teams
from app.ladder.models import Player
import dota2
import os

from steam import SteamClient, SteamID
from dota2 import Dota2Client

from dota2.enums import DOTA_GC_TEAM


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
        except KeyboardInterrupt:
            for bot in self.bots:
                bot.exit()
                bot.steam.logout()


    def start_bot(self, credentials):
        client = SteamClient()
        dota = Dota2Client(client)

        self.bots.append(dota)

        client.verbose_debug = True
        dota.verbose_debug = True

        @client.on('logged_on')
        def start_dota():
            dota.launch()

        @dota.on('ready')
        def dota_started():
            print 'Logged in: %s %s' % (dota.steam.username, dota.account_id)

            # if lobby is hung up from previous session, leave it
            dota.leave_practice_lobby()

            dota.create_practice_lobby(password='eu', options={
                'game_name': 'Inhouse Ladder',
                'game_mode': dota2.enums.DOTA_GameMode.DOTA_GAMEMODE_CD,
                'server_region': int(dota2.enums.EServerRegion.Europe),
                'fill_with_bots': False,
                'allow_spectating': True,
                'allow_cheats': False,
                'allchat': True,
                'dota_tv_delay': 2,
                'pause_setting': 1,
            })

        @dota.on(dota2.features.Lobby.EVENT_LOBBY_NEW)
        def lobby_new(lobby):
            print '%s joined lobby %s' % (dota.steam.username, lobby.lobby_id)

            dota.join_practice_lobby_team()  # jump to unassigned players
            dota.join_lobby_chat()

        # @dota.on(dota2.features.Lobby.EVENT_LOBBY_CHANGED)
        # def lobby_changed(lobby):
        #     if dota == bots[0]:
        #         print 'Lobby update!'
        #         print lobby

        @dota.on(dota2.features.Chat.EVENT_CHAT_JOINED)
        def chat_joined(channel):
            print '%s joined chat channel %s' % (dota.steam.username, channel.channel_name)
            # print channel

        @dota.on(dota2.features.Chat.EVENT_CHAT_MESSAGE)
        def chat_message(channel, sender, text, msg_obj):
            # process known commands
            if text.startswith('!balance'):
                self.balance_command(dota)
            elif text.startswith('!start'):
                dota.send_lobby_message('Start requested')
            else:
                dota.send_lobby_message('Fuck off, %s!' % sender)

        client.login(credentials['login'], credentials['password'])
        client.run_forever()

    @staticmethod
    def balance_command(bot):
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

        unregistered = [players_steam[p].name for p in players_steam.keys() if str(p) not in players]

        if unregistered:
            bot.send_lobby_message('I don\'t know these guys: %s' %
                                   ', '.join(unregistered))
            return

        print players

        # TODO: move this to BalancerResultManager
        players = [(p.name, p.ladder_mmr) for p in players.values()]

        # balance teams and save result
        mmr_exponent = 3
        answers = balance_teams(players, mmr_exponent)

        with transaction.atomic():
            result = models.BalanceResult.objects.create(mmr_exponent=mmr_exponent)
            for answer in answers:
                models.BalanceAnswer.objects.create(
                    teams=answer['teams'],
                    mmr_diff=answer['mmr_diff'],
                    mmr_diff_exp=answer['mmr_diff_exp'],
                    result=result
                )

        url = reverse('balancer:balancer-result', args=(result.id,))
        url = os.environ.get('BASE_URL', 'localhost:8000') + url

        answer = answers[0]
        for i, team in enumerate(answer['teams']):
            player_names = [p[0] for p in team['players']]
            bot.send_lobby_message('Team %d: %s' % (i+1, ' | '.join(player_names)))
        bot.send_lobby_message(url)