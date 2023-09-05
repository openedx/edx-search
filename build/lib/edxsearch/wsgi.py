"""
WSGI config for edxsearch project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/1.6/howto/deployment/wsgi/
"""

import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "edxsearch.settings")

from django.core.wsgi import get_wsgi_application
# application is the standard name to use here
application = get_wsgi_application()  # pylint: disable=invalid-name
