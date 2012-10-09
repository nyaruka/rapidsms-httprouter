from django.conf import settings
from django.db import transaction
from .models import Message
from rapidsms.models import Backend, Connection
from rapidsms.apps.base import AppBase
from rapidsms.messages.incoming import IncomingMessage
from rapidsms.messages.outgoing import OutgoingMessage
from rapidsms.log.mixin import LoggerMixin
from threading import Lock, Thread

from urllib import quote_plus
from urllib2 import urlopen
import time
import re

def fetch_url(url, params):
    """
    Wrapper around url open, mostly here so we can monkey patch over it in unit tests, though
    in some cases apps may monkey patch this to deal with secondary urls.
    """
    if getattr(settings, 'ROUTER_HTTP_METHOD', 'GET') == 'GET':
        response = urlopen(url, timeout=15)
    else:
        response = urlopen(url, " ", timeout=15)

    return response

class HttpRouter(object, LoggerMixin):
    """
    This is a simplified version of the normal SMS router in that it has no threading.  Instead
    it is expected that the handle_incoming and handle_outcoming calls are made in the HTTP
    thread.
    """
    incoming_phases = ("filter", "parse", "handle", "default", "cleanup")
    outgoing_phases = ("outgoing",)

    def __init__(self):
        # the apps we'll run through
        self.apps = []

        # we need to be started
        self.started = False

    @classmethod
    def normalize_number(cls, number):
        """
        Normalizes the passed in number, they should be only digits, some backends prepend + and
        maybe crazy users put in dashes or parentheses in the console.
        """
        return re.sub('[^0-9a-z]', '', number.lower())

    def add_message(self, backend, contact, text, direction, status):
        """
        Adds this message to the db.  This is both for logging, and we also keep state
        tied to it.
        """
        # lookup / create this backend
        # TODO: is this too flexible?  Perhaps we should do this upon initialization and refuse 
        # any backends not found in our settings.  But I hate dropping messages on the floor.
        backend, created = Backend.objects.get_or_create(name=backend)
        contact = HttpRouter.normalize_number(contact)

        # create our connection
        connection, created = Connection.objects.get_or_create(backend=backend, identity=contact)

        # force to unicode
        text = unicode(text)
        message = Message.objects.create(connection=connection,
                                         text=text,
                                         direction=direction,
                                         status=status)
        return message

    def mark_delivered(self, message_id):
        """
        Marks a message as delivered by the backend.
        """
        message = Message.objects.get(pk=message_id)
        message.status = 'D'
        message.delivered = datetime.datetime.now()
        message.save()

    def handle_incoming(self, backend, sender, text):
        """
        Handles an incoming message.
        """
        # create our db message for logging
        db_message = self.add_message(backend, sender, text, 'I', 'R')

        # and our rapidsms transient message for processing
        msg = IncomingMessage(db_message.connection, text, db_message.date)
        
        # add an extra property to IncomingMessage, so httprouter-aware
        # apps can make use of it during the handling phase
        msg.db_message = db_message
        
        self.info("SMS[%d] IN (%s) : %s" % (db_message.id, msg.connection, msg.text))
        try:
            for phase in self.incoming_phases:
                self.debug("In %s phase" % phase)
                if phase == "default":
                    if msg.handled:
                        self.debug("Skipping phase")
                        break

                for app in self.apps:
                    self.debug("In %s app" % app)
                    handled = False

                    try:
                        func = getattr(app, phase)
                        handled = func(msg)

                    except Exception, err:
                        import traceback
                        traceback.print_exc(err)
                        app.exception()

                    # during the _filter_ phase, an app can return True
                    # to abort ALL further processing of this message
                    if phase == "filter":
                        if handled is True:
                            self.warning("Message filtered")
                            raise(StopIteration)

                    # during the _handle_ phase, apps can return True
                    # to "short-circuit" this phase, preventing any
                    # further apps from receiving the message
                    elif phase == "handle":
                        if handled is True:
                            self.debug("Short-circuited")
                            # mark the message handled to avoid the 
                            # default phase firing unnecessarily
                            msg.handled = True
                            break
                    
                    elif phase == "default":
                        # allow default phase of apps to short circuit
                        # for prioritized contextual responses.   
                        if handled is True:
                            self.debug("Short-circuited default")
                            break
                        
        except StopIteration:
            pass

        db_message.status = 'H'
        db_message.save()
        
        db_responses = []

        # now send the message responses
        while msg.responses:
            response = msg.responses.pop(0)
            self.handle_outgoing(response, db_message)

        # we are no longer interested in this message... but some crazy
        # synchronous backends might be, so mark it as processed.
        msg.processed = True

        return db_message


    def add_outgoing(self, connection, text, source=None, status='Q'):
        """
        Adds a message to our outgoing queue, this is a non-blocking action
        """
        db_message = Message.objects.create(connection=connection,
                                            text=text,
                                            direction='O',
                                            status=status,
                                            in_response_to=source)
        self.info("SMS[%d] OUT (%s) : %s" % (db_message.id, str(connection), text))

        # process our outgoing phases
        self.process_outgoing_phases(db_message)

        # if it wasn't cancelled
        if db_message.status != 'C':
            # queue it
            db_message.status = 'Q'
            db_message.save()

        # if we have a router URL, send the message off
        if getattr(settings, 'ROUTER_URL', None):
            db_message.send()

        return db_message
                
    def handle_outgoing(self, msg, source=None):
        """
        Sends the passed in RapidSMS message off.  Optionally ties the outgoing message to the incoming
        message which triggered it.
        """
        # add it to our outgoing queue
        db_message = self.add_outgoing(msg.connection, msg.text, source, status='P')
        return db_message

    def process_outgoing_phases(self, outgoing):
        """
        Passes the passed in message through the outgoing phase for all our configured SMS apps.

        Apps have the opportunity to cancel messages in this phase by returning False when
        called with the message.  In that case this method will also return False
        """
        # create a RapidSMS outgoing message
        msg = OutgoingMessage(outgoing.connection, outgoing.text.replace('%','%%'))
        msg.db_message = outgoing
        
        send_msg = True
        for phase in self.outgoing_phases:
            self.debug("Out %s phase" % phase)

            # call outgoing phases in the opposite order of the incoming
            # phases, so the first app called with an  incoming message
            # is the last app called with an outgoing message
            for app in reversed(self.apps):
                self.debug("Out %s app" % app)

                try:
                    func = getattr(app, phase)
                    keep_sending = func(msg)

                    # we have to do things this way because by default apps return
                    # None from outgoing()
                    if keep_sending is False:
                        send_msg = False
                except Exception, err:
                    app.exception()

                # during any outgoing phase, an app can return True to
                # abort ALL further processing of this message
                if not send_msg:
                    outgoing.status = 'C'
                    outgoing.save()

                    self.warning("Message cancelled")
                    send_msg = False
                    break

        return send_msg

    def add_app(self, module_name):
        """
        Find the app named *module_name*, instantiate it, and add it to
        the list of apps to be notified of incoming messages. Return the
        app instance.
        """
        try:
            cls = AppBase.find(module_name)
        except:
            import traceback
            traceback.print_exc()
            cls = None

        if cls is None:
            self.error("Unable to find SMS application with module: '%s'" % module_name)
            return None

        app = cls(self)
        self.apps.append(app)
        return app


    def start(self):
        """
        Initializes our router.
        TODO: this happens in the HTTP thread on the first call, that could be bad.
        """
        # add all our apps
        for app_name in settings.SMS_APPS:
            self.add_app(app_name)

        # start all our apps
        for app in self.apps:
            app.start()

        # the list of messages which need to be sent, we load this from the DB
        # upon first starting up
        self.outgoing = [message for message in Message.objects.filter(status='Q')]

        # mark ourselves as started
        self.started = True
        
# we'll get started when we first get used
http_router = HttpRouter()
http_router_lock = Lock()

def get_router():
    """
    Takes care of performing lazy initialization of the www router.
    """
    global http_router
    global http_router_lock

    if not http_router.started:
        http_router_lock.acquire()
        try:
            if not http_router.started:
                http_router.start()
        finally:
            http_router_lock.release()

    return http_router
