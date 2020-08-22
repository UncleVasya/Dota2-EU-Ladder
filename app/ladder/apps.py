from django.apps import AppConfig


class LadderConfig(AppConfig):
    name = 'app.ladder'

    def ready(self):
        from . import signals
