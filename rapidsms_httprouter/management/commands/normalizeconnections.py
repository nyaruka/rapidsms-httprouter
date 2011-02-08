from optparse import make_option

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connections, router, transaction, DEFAULT_DB_ALIAS

from rapidsms.models import Connection, Backend
from rapidsms_httprouter.router import HttpRouter
from datetime import datetime

import traceback

class Command(BaseCommand):
    help = 'Normalizes all connections in the database, removing everything except digits.'

    def handle(self, *files, **options):
        connection = connections[DEFAULT_DB_ALIAS]

        # Start transaction management. All fixtures are installed in a
        # single transaction to ensure that all references are resolved.
        transaction.commit_unless_managed()
        transaction.enter_transaction_management()
        transaction.managed(True)

        # for every connection
        for connection in Connection.objects.all():
            # try normalizing the number
            normalized = HttpRouter.normalize_number(connection.identity)

            # if it is different, then we changed it, first check to see
            # if there is a connection that is identical.  In that case we don't
            # change anything, it is too difficult to know who might have a reference
            # to this connection in the system to remap.
            if normalized != connection.identity:
                collision = Connection.objects.filter(identity=normalized, backend=connection.backend)

                # no collision!  Ok, we can just save our new identity
                if not collision:
                    print "remapping %s to %s" % (connection.identity, normalized)
                    
                    connection.identity = normalized
                    connection.save()

                else:
                    print "skipping %s, collision" % (connection.identity)

        transaction.commit()


