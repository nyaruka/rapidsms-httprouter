from django.conf import settings
from .models import Message
from rapidsms.models import Backend, Connection
from rapidsms.apps.base import AppBase
from rapidsms.messages.incoming import IncomingMessage
from rapidsms.log.mixin import LoggerMixin
from threading import Lock

from urllib import urlencode
from urllib2 import urlopen

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

        self.outgoing = None

        # we need to be started
        self.started = False

    def add_message(self, backend, contact, text, direction, status):
        """
        Adds this message to the db.  This is both for logging, and we also keep state
        tied to it.
        """
        # lookup / create this backend
        # TODO: is this too flexible?  Perhaps we should do this upon initialization and refuse 
        # any backends not found in our settings.  But I hate dropping messages on the floor.
        backend, created = Backend.objects.get_or_create(name=backend)
        
        # create our connection
        connection, created = Connection.objects.get_or_create(backend=backend, identity=contact)

        # finally, create our db message
        message = Message.objects.create(connection=connection,
                                         text=text,
                                         direction='I',
                                         status=status)
        return message


    def mark_sent(self, message_id):
        """
        Marks a message as sent by the backend.
        """
        message = Message.objects.get(pk=message_id)
        message.status = 'S'
        message.save()


    def handle_incoming(self, backend, sender, text):
        """
        Handles an incoming message.
        """
        # create our db message for logging
        db_message = self.add_message(backend, sender, text, 'I', 'R')

        # and our rapidsms transient message for processing
        msg = IncomingMessage(db_message.connection, text, db_message.date)

        self.info("Incoming (%s): %s" % (msg.connection, msg.text))

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
        
    def handle_outgoing(self, msg, source=None):
        """
        Sends out the passed in message.
        """
        
        # first things first, log it (TODO, should this be elsewhere?)
        db_message = Message.objects.create(connection=msg.connection,
                                            text=msg.text,
                                            direction='O',
                                            status='Q',
                                            in_response_to=source)

        self.info("Outgoing (%s): %s" % (msg.connection, msg.text))

        for phase in self.outgoing_phases:
            self.debug("Out %s phase" % phase)
            continue_sending = True

            # call outgoing phases in the opposite order of the incoming
            # phases, so the first app called with an  incoming message
            # is the last app called with an outgoing message
            for app in reversed(self.apps):
                self.debug("Out %s app" % app)

                try:
                    func = getattr(app, phase)
                    continue_sending = func(msg)

                except Exception, err:
                    app.exception()

                # during any outgoing phase, an app can return True to
                # abort ALL further processing of this message
                if continue_sending is False:
                    db_message.status = 'C'
                    db_message.save()
                    self.warning("Message cancelled")
                    return False

        # add the message to our outgoing queue
        self.send_message(db_message)
        return db_message

    def send_message(self, msg):
        """
        Sends the message off.  We first try to directly contact our sms router to deliver it, 
        if we fail, then we just add it to our outgoing queue.
        """

        if not getattr(settings, 'ROUTER_URL', None):
            print "No ROUTER_URL set in settings.py, queuing message for later delivery."
            return

        params = {
            'backend': msg.connection.backend,
            'recipient': msg.connection.identity,
            'text': msg.text,
            'id': msg.pk
        }

        try:
            response = urlopen(settings.ROUTER_URL + urlencode(params))

            if response.getcode() == 200:
                self.info("Message: %s sent: " % msg.id)
            else:
                self.outgoing.append(msg)
                self.error("Message not sent, got status: %s .. queued for later delivery." % response.getcode())

        except Exception as e:
            self.outgoing.append(msg)
            self.error("Message not sent: %s .. queued for later delivery." % str(e))

    def add_app(self, module_name):
        """
        Find the app named *module_name*, instantiate it, and add it to
        the list of apps to be notified of incoming messages. Return the
        app instance.
        """
        cls = AppBase.find(module_name)
        if cls is None: return None

        app = cls(self)
        self.apps.append(app)
        return app


    def start(self):
        """
        Initializes our router.
        """

        # add all our apps
        for app_name in settings.INSTALLED_APPS:
            self.add_app(app_name)

        # start all our apps
        for app in self.apps:
            app.start()

        # the list of messages which need to be sent, we load this from the DB
        # upon first starting up
        self.outgoing = [message for message in Message.objects.filter(status='Q')]

        # mark ourselves as started
        self.started = True
        
# we'll get init on the first around
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
