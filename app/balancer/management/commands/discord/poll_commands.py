import discord

from app.ladder.models import QueueChannel, LadderSettings, DiscordPoll, DiscordChannels

class PollService:
    def __init__(self, bot):
        self.bot = bot
        polls_channel = DiscordChannels.get_solo().polls
        self.polls_channel = self.bot.get_channel(polls_channel)

    async def polls_welcome_show(self):
        text = 'Hello, friends!\n\n' + \
               'Here you can vote for inhouse settings.\n\n' + \
               'These polls are directly connected to our system.' + \
               '\n\n.'
        msg_id = DiscordPoll.objects.get(name='Welcome').message_id
        msg = await self.polls_channel.fetch_message(msg_id)
        await msg.edit(content=text)

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

    async def on_faceit_reaction(self, message, user, player=None):
        pass

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

    async def on_poll_reaction_add(self, message, user, payload, player):
        poll = DiscordPoll.objects.filter(message_id=message.id).first()

        if not poll:
            return

        for r in message.reactions:
            if r.emoji != payload.emoji.name:
                await r.remove(user)

        await self.poll_reaction_funcs[poll.name](message, user, player)

    async def on_poll_reaction_remove(self, message, user, payload, player):
        poll = DiscordPoll.objects.filter(message_id=message.id).first()

        # if not a poll message, ignore reaction
        if not poll:
            return

        # call reaction processing function
        await self.poll_reaction_funcs[poll.name](message, user)

