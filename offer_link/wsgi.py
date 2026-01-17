"""
WSGI config for offer_link project.
"""

import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'offer_link.settings')
application = get_wsgi_application()