from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connections


class Command(BaseCommand):
    help = "Grant runtime CRUD privileges to the app role (idempotent). Runs as owner."

    def handle(self, *args, **options):
        role = settings.DB_APP_ROLE
        sql = f"""
        GRANT USAGE ON SCHEMA public TO {role};
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role};
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role};
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role};
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT USAGE, SELECT ON SEQUENCES TO {role};
        """
        with connections["admin"].cursor() as cursor:
            cursor.execute(sql)
        self.stdout.write(
            self.style.SUCCESS(f"Granted runtime privileges to role '{role}'.")
        )
