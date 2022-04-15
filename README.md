## Dota2 EU Ladder

A full-featured inhouse lobby system for Dota2 leagues.

### Key Features
-------------
- Discord bot - signup, queue for games, check your stats or control the league right from discord.
- Dota bot - automated lobbies that can invite players once queue is full, check for correct teams setup, record game results, handle unfamiliar/banned players and more.
- A dotabuff-like website with league and players stats (matches played, winrates, best teammates / strongest opponents, league health, etc).
- An admin panel for the staff to have more control over their league: setup different inhouse queues, edit players signups, manage league settings.
- Team balancer - produces competitive teams from a set of queued players, with regards to players preferred roles.

### Installing
----------

**Python 3.7.1 or higher is required**

- Initial project setup: https://github.com/UncleVasya/Dota2-EU-Ladder/wiki/Initial-project-setup
- Project deploy on OpenShift: https://github.com/UncleVasya/Dota2-EU-Ladder/wiki/Project-deploy-on-OpenShift

### Usage
----------
After initial setup is done, you have to keep 3 scripts running:

- discord bot: `python manage.py discord_bot`
- dota bot: `python manage.py dota_bot`
- website (optional): `python manage.py runserver` 

  If deployed on real hosting you would want to use a real web server like gunicorn: 
  ```
  gunicorn -b 0.0.0.0:8000 dota2_eu_ladder.wsgi:application
  ```
  
### Powered by:
------

- [ValvePython/dota2](https://github.com/ValvePython/dota2) - A module for interacting with Dota2's Game Coordinator.
- [discord.py](https://github.com/Rapptz/discord.py) - A modern, easy to use and async ready API wrapper for Discord.
- [Django](https://github.com/django/django) - A high-level Python web framework.
- [RD2L](https://discord.com/invite/6Q9jCyuDSt), [Clarity](https://discord.gg/rh5te7swjq) and [Doghouse](https://discord.gg/A6cKTRhkcX) dota communities - thank you for using this project, giving ideas for new features and for reporting bugs.

### Screenshots
----------
- Inhouse queue is about to pop, one spot left:
    ![](http://ipic.su/img/img7/fs/clipboard.1645524828.png)

- Queue popped. Two fair teams produced, players are notified on discord (and got invited in game by dota bot).
    ![](http://ipic.su/img/img7/fs/clipboard.1645524976.png)

- Website with stats:
    ![](http://ipic.su/img/img7/fs/clipboard.1645525486.png) 
    ![](http://ipic.su/img/img7/fs/clipboard.1645525605.png) 
    ![](http://ipic.su/img/img7/fs/clipboard3.1645526747.png)
