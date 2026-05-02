"""
Management command: ensure_superuser
Creates a superuser from environment variables if no superuser exists yet.

Required environment variables:
  DJANGO_SUPERUSER_USERNAME  – username for the superuser account
  DJANGO_SUPERUSER_EMAIL     – email address (may be empty string)
  DJANGO_SUPERUSER_PASSWORD  – password for the superuser account

If any of the required variables are absent, or if a superuser already
exists, the command exits silently without making any changes.
"""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create a superuser from environment variables (idempotent)."

    def handle(self, *args, **options):
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "").strip()
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "").strip()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "").strip()

        if not username or not password:
            self.stdout.write(
                "ensure_superuser: DJANGO_SUPERUSER_USERNAME or "
                "DJANGO_SUPERUSER_PASSWORD not set — skipping."
            )
            return

        User = get_user_model()

        if User.objects.filter(is_superuser=True).exists():
            self.stdout.write("ensure_superuser: superuser already exists — skipping.")
            return

        User.objects.create_superuser(username=username, email=email, password=password)
        self.stdout.write(
            self.style.SUCCESS(f"ensure_superuser: created superuser '{username}'.")
        )
