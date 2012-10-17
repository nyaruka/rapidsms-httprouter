import StringIO
from celery.task import task
from datetime import datetime, timedelta
from django.conf import settings
from .models import Message, DeliveryError
from .router import HttpRouter
from urllib import quote_plus
from urllib2 import urlopen
import traceback
import time
import re
import redis

import logging
logger = logging.getLogger(__name__)

def build_send_url(params, **kwargs):
    """
    Constructs an appropriate send url for the given message.
    """
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
        backend_name = params['backend']
            
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

def send_message(msg, **kwargs):
    """
    Sends a message using its configured endpoint
    """
    msg_log = "Sending message: [%d]\n" % msg.id

    print "[%d] >> %s\n" % (msg.id, msg.text)

    # and actually hand the message off to our router URL
    try:
        params = {
            'backend': msg.connection.backend.name,
            'recipient': msg.connection.identity,
            'text': msg.text,
            'id': msg.pk
        }

        url = build_send_url(params)
        print "[%d] - %s\n" % (msg.id, url)
        msg_log += "%s %s\n" % (msg.connection.backend.name, url)

        response = HttpRouter.fetch_url(url, params)
        status_code = response.getcode()

        body = response.read().decode('ascii', 'ignore').encode('ascii')

        msg_log += "Status Code: %d\n" % status_code
        msg_log += "Body: %s\n" % body

        # kannel likes to send 202 responses, really any
        # 2xx value means things went okay
        if int(status_code/100) == 2:
            print "  [%d] - sent %d" % (msg.id, status_code)
            logger.info("SMS[%d] SENT" % msg.id)
            msg.sent = datetime.now()
            msg.status = 'S'
            msg.save()

            return body
        else:
            raise Exception("Received status code: %d" % status_code)
    except Exception as e:
        import traceback
        traceback.print_exc(e)
        print "  [%d] - send error - %s" % (msg.id, str(e))

        # previous errors
        previous_count = DeliveryError.objects.filter(message=msg).count()
        msg_log += "Failure #%d\n\n" % (previous_count+1)
        msg_log += "Error: %s\n\n" % str(e)
        
        if previous_count >= 2:
            msg_log += "Permanent failure, will not retry."
            msg.status = 'F'
            msg.save()
        else:
            msg_log += "Will retry %d more time(s)." % (2 - previous_count)
            msg.status = 'E'
            msg.save()

        DeliveryError.objects.create(message=msg, log=msg_log)

    return None

@task(track_started=True)
def send_message_task(message_id):  #pragma: no cover
    # noop if there is no ROUTER_URL
    if not getattr(settings, 'ROUTER_URL', None):
        return

    # we use redis to acquire a global lock based on our settings key
    r = redis.StrictRedis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB)

    # try to acquire a lock, at most it will last 60 seconds
    with r.lock('send_message_%d' % message_id, timeout=60):
        # get the message
        msg = Message.objects.get(pk=message_id)

        # if it hasn't been sent and it needs to be sent
        if msg.status == 'Q':
            body = send_message(msg)

@task(track_started=True)
def resend_errored_messages_task():  #pragma: no cover
    # noop if there is no ROUTER_URL
    if not getattr(settings, 'ROUTER_URL', None):
        return

    print "[[resending errored messages]]"

    # we use redis to acquire a global lock based on our settings key
    r = redis.StrictRedis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=settings.REDIS_DB)

    # try to acquire a lock, at most it will last 5 mins
    with r.lock('resend_messages', timeout=300):
        # get all errored outgoing messages
        pending = Message.objects.filter(direction='O', status__in=('E'))

        # send each
        for msg in pending:
            msg.send()

        # and all queued messages that are older than 5 mins
        five_minutes_ago = datetime.now() - timedelta(minutes=5)
        pending = Message.objects.filter(direction='O', status__in=('Q'), updated__lte=five_minutes_ago)

        # send each
        for msg in pending:
            msg.send()
