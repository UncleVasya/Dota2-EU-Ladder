"""
WSGI config for dota2_eu_ladder project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/1.9/howto/deployment/wsgi/


MAREK: Commented out for deploying purposes, bring it back locally
"""

# import sys
# import os
# from os.path import dirname, realpath
#
# from django.core.wsgi import get_wsgi_application
#
#
# sys.path.append(dirname(realpath(__file__)))
# os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dota2_eu_ladder.settings")
#
# virtenv = os.environ.get('OPENSHIFT_PYTHON_DIR', '.') + '/virtenv/'
# virtualenv = os.path.join(virtenv, 'bin/activate_this.py')
# try:
#     exec(compile(open(virtualenv).read(), virtualenv, 'exec'), dict(__file__=virtualenv))
# except IOError:
#     pass
#
# application = get_wsgi_application()
