from django.apps import AppConfig


class ChatConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "chat"

    def ready(self) -> None:
        # Recupera execuções abandonadas por encerramento inesperado (RF-12).
        # Evita tocar no banco antes das migrations (ex.: primeiro migrate).
        import warnings

        from django.db import connection

        from chat.services.recovery import recover_abandoned_runs

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                if "chat_generationrun" not in connection.introspection.table_names():
                    return
                recover_abandoned_runs()
        except Exception:
            pass
