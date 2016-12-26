from django.apps import AppConfig


class LadderConfig(AppConfig):
    name = 'app.ladder'

    def ready(self):
        import signals
