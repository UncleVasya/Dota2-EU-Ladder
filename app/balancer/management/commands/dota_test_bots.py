from django.core.management.base import BaseCommand
import gevent
import dota2
from steam import SteamClient
from dota2 import Dota2Client


class Command(BaseCommand):
    def __init__(self):
        self.bots = []
        self.lobby = None
        self.password = None

    def add_arguments(self, parser):
        parser.add_argument('-l', '--lobby', type=int)
        parser.add_argument('-p', '--password',
                            nargs='?', type=str, default='eu', const='eu')

    def handle(self, *args, **options):
        self.lobby = options['lobby']
        self.password = options['password']

        bots_num = 9

        credentials = [
            {
                'login': 'login%d' % i,
                'password': 'password%d' % i,
            } for i in xrange(2, bots_num+2)
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
            dota.join_practice_lobby(self.lobby, self.password)

        @dota.on(dota2.features.Lobby.EVENT_LOBBY_NEW)
        def lobby_new(lobby):
            print '%s joined lobby %s' % (dota.steam.username, lobby.lobby_id)

            ind = self.bots.index(dota)
            team = ind / 5
            slot = ind % 5 + 1
            dota.join_practice_lobby_team(slot, team)

        client.login(credentials['login'], credentials['password'])
        client.run_forever()
