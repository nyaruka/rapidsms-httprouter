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

# we make sure only one thread is modifying db messages a time
outgoing_db_lock = Lock()

# our worker threads
outgoing_worker_threads = []

# whether we are currently sending mass messages, where we will queue up
# many messages at once, used as an optimization
sending_mass_messages = False

def start_sending_mass_messages():
    global sending_mass_messages
    sending_mass_messages = True

def stop_sending_mass_messages():
    global sending_mass_messages
    sending_mass_messages = False

class HttpRouterThread(Thread, LoggerMixin):
    """
    This thread is just a worker thread for messages.  The run() method pops off a message to work on
    and continues appropriately.
    """

    def __init__(self, **kwargs):
        Thread.__init__(self, **kwargs)
        self._isbusy = False

    def is_busy(self):
        return self._isbusy

    def run(self):
        global sending_mass_messages

        while self.is_alive():
            if not sending_mass_messages:
                transaction.enter_transaction_management()

                try:
                    # this gets any outgoing messages which are either pending or queued
                    to_process = list(Message.objects.filter(direction='O',
                                                             status__in=['P','Q']).order_by('status').for_single_update())

                    if len(to_process):
                        self._isbusy = True
                        outgoing_message = to_process[0]
                        outgoing_message.status = 'L'
                        outgoing_message.save()

                        # frees our update lock
                        transaction.commit()                        

                        # process the outgoing phases for this message
                        send_msg = get_router().process_outgoing_phases(outgoing_message)

                        # if it wasn't cancelled, send it off
                        if send_msg:
                            self.send_message(outgoing_message)
                except:
                    import traceback
                    traceback.print_exc()

                finally:
                    try:
                        transaction.commit()
                    except:
                        pass

            self._isbusy = False
            time.sleep(0.5)

    def fetch_url(self, url):
        """
        Wrapper around url open, mostly here so we can monkey patch over it in unit tests.
        """
        response = urlopen(url, timeout=15)
        return response.getcode()

    def build_send_url(self, msg, kwargs):
        """
        Constructs an appropriate send url for the given message.
        """
        # first build up our list of parameters
        params = {
            'backend': msg.connection.backend.name,
            'recipient': msg.connection.identity,
            'text': msg.text,
            'id': msg.pk
        }

        # make sure our parameters are URL encoded
        params.update(kwargs)
        for k, v in params.items():
            try:
                params[k] = quote_plus(str(v))
            except UnicodeEncodeError:
                params[k] = quote_plus(str(v.encode('UTF-8')))

        # get our router URL
        router_url = settings.ROUTER_URL

        # is this actually a dict?  if so, we want to look up the appropriate backend
        if type(router_url) is dict:
            router_dict = router_url
            backend_name = msg.connection.backend.name
            
            # is there an entry for this backend?
            if backend_name in router_dict:
                router_url = router_dict[backend_name]

            # if not, look for a default backend 
            elif 'default' in router_dict:
                router_url = router_dict['default']

            # none?  blow the hell up
            else:
                self.error("No router url mapping found for backend '%s', check your settings.ROUTER_URL setting" % backend_name)
                raise Exception("No router url mapping found for backend '%s', check your settings.ROUTER_URL setting" % backend_name)

        # return our built up url with all our variables substituted in
        full_url = router_url % params

        return full_url

    def send_message(self, msg, **kwargs):
        """
        Sends the message off.  We first try to directly contact our sms router to deliver it, 
        if we fail, then we just add it to our outgoing queue.
        """
        global outgoing_db_lock

        # and actually hand the message off to our router URL
        try:
            url = self.build_send_url(msg, kwargs)
            status_code = self.fetch_url(url)
            outgoing_db_lock.acquire()
            # kannel likes to send 202 responses, really any
            # 2xx value means things went okay
            if int(status_code/100) == 2:
                self.info("SMS[%d] SENT" % msg.id)
                msg.status = 'S'
                msg.save()
            else:
                self.error("SMS[%d] Message not sent, got status: %s .. queued for later delivery." % (msg.id, status_code))
                msg.status = 'Q'
                msg.save()
            outgoing_db_lock.release()
        except Exception as e:
            self.error("SMS[%d] Message not sent: %s .. queued for later delivery." % (msg.id, str(e)))
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
        global outgoing_db_lock

        # lookup / create this backend
        # TODO: is this too flexible?  Perhaps we should do this upon initialization and refuse 
        # any backends not found in our settings.  But I hate dropping messages on the floor.
        backend, created = Backend.objects.get_or_create(name=backend)

        contact = HttpRouter.normalize_number(contact)

        # create our connection
        connection, created = Connection.objects.get_or_create(backend=backend, identity=contact)

        # finally, create our db message
        outgoing_db_lock.acquire()

        message = Message.objects.create(connection=connection,
                                         text=text,
                                         direction=direction,
                                         status=status)
        outgoing_db_lock.release()

        return message


    def mark_delivered(self, message_id):
        """
        Marks a message as delivered by the backend.
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

        outgoing_db_lock.acquire()
        db_message.status = 'H'
        db_message.save()
        outgoing_db_lock.release()
        
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
        global outgoing_db_lock
        outgoing_db_lock.acquire()
        db_message = Message.objects.create(connection=connection,
                                            text=text,
                                            direction='O',
                                            status=status,
                                            in_response_to=source)
        outgoing_db_lock.release()

        self.info("SMS[%d] OUT (%s) : %s" % (db_message.id, str(connection), text))

        global outgoing_worker_threads

        # if we have no ROUTER_URL configured, then immediately process our outgoing phases
        # and leave the message in the queue
        if not getattr(settings, 'ROUTER_URL', None):
            if self.process_outgoing_phases(db_message):
                outgoing_db_lock.acquire()
                db_message.status = 'Q'
                db_message.save()
                outgoing_db_lock.release()

        # otherwise, fire up any threads we need to send the message out
        else:
            # check for available worker threads in the pool, add one if necessary
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

                    outgoing_db_lock.acquire()
                    outgoing.save()
                    outgoing_db_lock.release()

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
