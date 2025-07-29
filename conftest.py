import os
import django

# Set the Django settings module for test environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'edxsearch.settings')

# Setup Django
django.setup()
