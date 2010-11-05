from django.conf import settings
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

outgoing_pk_queue = []
outgoing_queue_lock = Lock()
outgoing_db_lock = Lock()
outgoing_worker_threads = []
sending_mass_messages = False

def start_sending_mass_messages():
    global sending_mass_messages
    sending_mass_messages = True

def stop_sending_mass_messages():
    global sending_mass_messages
    sending_mass_messages = False

class HttpRouterThread(Thread, LoggerMixin):

    outgoing_phases = ("outgoing",)

    def __init__(self, **kwargs):
        Thread.__init__(self, **kwargs)
        self._isbusy = False

    def is_busy(self):
        return self._isbusy

    def run(self):
        global outgoing_pk_queue
        global outgoing_queue_lock
        global outgoing_db_lock
        global sending_mass_messages
        while self.is_alive():
            if not sending_mass_messages:
                print "*************worker thread doing work"
                outgoing_queue_lock.acquire()

                to_process = Message.objects.filter(direction='O',status__in=['P','Q']).order_by('status').exclude(pk__in=outgoing_pk_queue)
                if to_process.count():
                    self._isbusy = True
                    outgoing_message = to_process[0]
                    outgoing_pk_queue.append(outgoing_message.pk)
                    outgoing_queue_lock.release()

                    msg = OutgoingMessage(outgoing_message.connection, outgoing_message.text)
                    msg.db_message = outgoing_message
                    self.info("Outgoing (%s): %s" % (msg.connection, msg.text))

                    cancelled = False
                    for phase in self.outgoing_phases:
                        self.debug("Out %s phase" % phase)
                        continue_sending = True

                        # call outgoing phases in the opposite order of the incoming
                        # phases, so the first app called with an  incoming message
                        # is the last app called with an outgoing message
                        for app in reversed(get_router().apps):
                            self.debug("Out %s app" % app)

                            try:
                                func = getattr(app, phase)
                                continue_sending = func(msg)

                            except Exception, err:
                                app.exception()

                            # during any outgoing phase, an app can return True to
                            # abort ALL further processing of this message
                            if continue_sending is False:
                                outgoing_message.status = 'C'

                                outgoing_db_lock.acquire()
                                outgoing_message.save()
                                outgoing_db_lock.release()

                                self.warning("Message cancelled")
                                cancelled = True
                                break
                    if not cancelled:
                        # add the message to our outgoing queue
                        self.send_message(outgoing_message)

                    outgoing_queue_lock.acquire()
                    outgoing_pk_queue.remove(outgoing_message.pk)
                    outgoing_queue_lock.release()

                else:
                    outgoing_queue_lock.release()
            self._isbusy = False
            time.sleep(0.5)

    def send_message(self, msg, **kwargs):
        """
        Sends the message off.  We first try to directly contact our sms router to deliver it, 
        if we fail, then we just add it to our outgoing queue.
        """
        global outgoing_db_lock
        if not getattr(settings, 'ROUTER_URL', None):
            msg.status = 'Q'
            outgoing_db_lock.acquire()
            msg.save()
            outgoing_db_lock.release()
            return

        params = {
            'backend': msg.connection.backend,
            'recipient': msg.connection.identity,
            'text': msg.text,
            'id': msg.pk
        }

        # add any other backend-specific parameters from kwargs
        params.update(kwargs)

        for k, v in params.items():
            params[k] = quote_plus(str(v))
        try:
            #FIXME: clean this up
            response = urlopen(settings.ROUTER_URL % params)
            outgoing_db_lock.acquire()
            # kannel likes to send 202 responses, really any
            # 2xx value means things went okay
            if int(response.getcode()/100) == 2:
                self.info("Message: %s sent: " % msg.id)
                msg.status = 'S'
                msg.save()
            else:
                self.error("Message not sent, got status: %s .. queued for later delivery." % response.getcode())
                msg.status = 'Q'
                msg.save()
            outgoing_db_lock.release()
        except Exception as e:
            self.error("Message not sent: %s .. queued for later delivery." % str(e))
            outgoing_db_lock.acquire()
            msg.status = 'Q'
            msg.save()
            outgoing_db_lock.release()

class HttpRouter(object, LoggerMixin):
    """
    This is a simplified version of the normal SMS router in that it has no threading.  Instead
    it is expected that the handle_incoming and handle_outcoming calls are made in the HTTP
    thread.
    """

    incoming_phases = ("filter", "parse", "handle", "default", "cleanup")

    def __init__(self):
        # the apps we'll run through
        self.apps = []

        # we need to be started
        self.started = False

    def add_message(self, backend, contact, text, direction, status):
        """
        Adds this message to the db.  This is both for logging, and we also keep state
        tied to it.
        """
        global outgoing_db_lock
        # lookup / create this backend
        # TODO: is this too flexible?  Perhaps we should do this upon initialization and refuse 
        # any backends not found in our settings.  But I hate dropping messages on the floor.
        backend, created = Backend.objects.get_or_create(name=backend)
        
        # some backends append a + to numbers, some don't
        # this leads to duplications of numbers
        if contact[0:1] == '+':
            contact = contact[1:]
        # create our connection
        connection, created = Connection.objects.get_or_create(backend=backend, identity=contact)
        # finally, create our db message
        outgoing_db_lock.acquire()
        message = Message.objects.create(connection=connection,
                                         text=text,
                                         direction='I',
                                         status=status)
        outgoing_db_lock.release()
        return message


    def mark_sent(self, message_id):
        """
        Marks a message as sent by the backend.
        """
        global outgoing_db_lock

        message = Message.objects.get(pk=message_id)
        outgoing_db_lock.acquire()
        message.status = 'D'
        message.save()
        outgoing_db_lock.release()


    def handle_incoming(self, backend, sender, text):
        """
        Handles an incoming message.
        """
        global outgoing_db_lock
        # create our db message for logging
        db_message = self.add_message(backend, sender, text, 'I', 'R')

        # and our rapidsms transient message for processing
        msg = IncomingMessage(db_message.connection, text, db_message.date)
        
        # add an extra property to IncomingMessage, so httprouter-aware
        # apps can make use of it during the handling phase
        msg.db_message = db_message
        
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

        outgoing_db_lock.acquire()
        db_message.status = 'H'
        db_message.save()
        outgoing_db_lock.release()
        db_responses = []
        
        # respond with a default message if one exists
        if (not msg.responses) and getattr(settings, 'DEFAULT_RESPONSE', None):
            msg.responses.append(OutgoingMessage(db_message.connection, settings.DEFAULT_RESPONSE))
            
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
        global outgoing_db_lock
        outgoing_db_lock.acquire()
        db_message = Message.objects.create(connection=connection,
                                            text=text,
                                            direction='O',
                                            status=status,
                                            in_response_to=source)
        outgoing_db_lock.release()
        return db_message
                
    def handle_outgoing(self, msg, source=None):
        """
        Passes the message through the appropriate outgoing steps for all our apps,
        then sends it off if it wasn't cancelled.
        """
        global outgoing_worker_threads
        
        # add it to our db/queue
        db_message = self.add_outgoing(msg.connection, msg.text, source, status='P')

        #check for available worker threads in the pool, add one if necessary
        num_workers = getattr(settings, 'ROUTER_WORKERS', 5)
        all_busy = True
        for worker in outgoing_worker_threads:
            if not worker.is_busy():
                 all_busy = False
                 break
        if all_busy and len(outgoing_worker_threads) < num_workers:
            worker = HttpRouterThread()
            worker.daemon = True # they don't need to quit gracefully
            worker.start()
            outgoing_worker_threads.append(worker)
        
        return db_message

    def add_app(self, module_name):
        """
        Find the app named *module_name*, instantiate it, and add it to
        the list of apps to be notified of incoming messages. Return the
        app instance.
        """
        try:
            cls = AppBase.find(module_name)
        except:
            cls = None

        if cls is None: return None

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
