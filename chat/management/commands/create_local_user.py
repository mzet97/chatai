"""Bootstrap do usuário local: senha interativa, sem senha padrão embutida (RNF-01)."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Cria o usuário local (uso pessoal, sem cadastro público)."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="local", help="Nome do usuário local")

    def handle(self, *args, **options):
        User = get_user_model()
        username = options["username"]
        if User.objects.filter(username=username).exists():
            raise CommandError(f"Usuário '{username}' já existe.")
        import getpass

        password = getpass.getpass(f"Senha para '{username}': ")
        if not password:
            raise CommandError("Senha vazia não é permitida.")
        User.objects.create_user(username=username, password=password)
        self.stdout.write(self.style.SUCCESS(f"Usuário '{username}' criado."))
