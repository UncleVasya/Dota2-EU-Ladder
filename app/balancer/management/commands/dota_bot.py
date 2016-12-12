from time import sleep
from django.core.management.base import BaseCommand
import thread
import dota2

from steam import SteamClient
from dota2 import Dota2Client


class Command(BaseCommand):
    def handle(self, *args, **options):
        bots_num = 10

        credentials = [
            {
                'login': '***%d' % i,
                'password': '***%d' % i,
            } for i in xrange(1, bots_num+1)
        ]

        bots = []

        def start_bot(credentials):
            client = SteamClient()
            dota = Dota2Client(client)

            bots.append(dota)

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

                if dota == bots[0]:
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
                if dota != bots[0]:
                    return

                print 'New lobby!'
                print lobby

                dota.join_practice_lobby_team()  # jump to unassigned players
                dota.join_lobby_chat()

                # let's wait for other bots to login
                while sum(1 if bot.ready else 0 for bot in bots) < 10:
                    sleep(1)

                for i, bot in enumerate(bots[1:]):
                    print '%s is joining lobby' % bot.steam.username
                    bot.join_practice_lobby(lobby.lobby_id, lobby.pass_key)

                    team = i / 5
                    slot = i % 5 + 1
                    bot.join_practice_lobby_team(slot, team)

            # @dota.on(dota2.features.Lobby.EVENT_LOBBY_CHANGED)
            # def lobby_changed(lobby):
            #     if dota == bots[0]:
            #         print 'Lobby update!'
            #         print lobby

            @dota.on(dota2.features.Chat.EVENT_CHAT_JOINED)
            def chat_joined(channel):
                print 'Joined chat!'
                print channel

            @dota.on(dota2.features.Chat.EVENT_CHAT_MESSAGE)
            def chat_message(channel, sender, text, msg_obj):
                # process known commands
                if text.startswith('!balance'):
                    dota.send_lobby_message('Balance requested')
                elif text.startswith('!start'):
                    dota.send_lobby_message('Start requested')
                else:
                    dota.send_lobby_message('Fuck off, %s!' % sender)

            client.login(credentials['login'], credentials['password'])
            client.run_forever()

        for c in credentials:
            thread.start_new_thread(start_bot, (c,))

        while True:
            sleep(0.1)
