from django import forms
from django.http import HttpResponse
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from urlparse import urlparse

from .models import Message, SENT, FAILED, DELIVERED
from .router import get_router

import requests
import json

def parse_textit_router_url(router_url):
    """
    Router URLs take the form:
      http://250788383383:dcfaa3ae960e3205bc4dc04c2ebefe1df22c7a2f@textit.in/api/v1
    """
    if not router_url:
        return None

    textit_offset = router_url.find('@textit.in')

    if textit_offset > 0:
        # parse the url
        parsed = urlparse(router_url)

        if not parsed.username or not parsed.password:
            raise Exception("Invalid configuration for TextIt endpoint, must be in the format: "\
                            "'http://[phone]:[api_token]@textit.in/api/v1' was '%s'" % router_url)

        # strip the + for consistency.. most will probably forget to URL encode it anyways
        phone = parsed.username.strip('+ ')

        return dict(phone=phone,
                    token=parsed.password)

    # this isn't a textit URL, nothing to see here
    return None

# lazy loaded caches since these two lookups are very common
__backends_by_name = dict()
__backends_by_phone = dict()


def lookup_textit_backend_by_phone(phone):
    """
    Looks through our ROUTER_URL parameters for the backend that matches the passed in phone
    number.  If not found, returns None.
    """
    # strip off leading + from phone number if it is there
    phone = phone.strip('+ ')

    if phone in __backends_by_phone: return __backends_by_phone[phone]

    # look through our router urls
    for backend in settings.ROUTER_URL.keys():
        router_url = settings.ROUTER_URL[backend]
        router_backend = parse_textit_router_url(router_url)
        
        if router_backend and router_backend['phone'] == phone:
            textit_backend = router_backend
            textit_backend['name'] = backend
            break

    __backends_by_phone[phone] = textit_backend
    return textit_backend




def lookup_textit_backend_by_name(name):
    """
    Looks through our ROUTER_URL parameters for the backend that matches the passed in name.
    If not found, returns None.
    """
    if name in __backends_by_name: return __backends_by_name[name]
    router_url = settings.ROUTER_URL

    if isinstance(router_url, dict):
        router_url = settings.ROUTER_URL.get(name, None)

    if router_url:
	textit_backend = parse_textit_router_url(router_url)
        if textit_backend:
            textit_backend['name'] = name

    __backends_by_name[name] = textit_backend
    return textit_backend

@csrf_exempt
def textit_webhook(request):
    json_response = dict()

    # check our password
    config_password = getattr(settings, 'ROUTER_PASSWORD', None)
    if config_password:
        request_password = request.REQUEST.get('password', None)
        if request_password != config_password:
            return HttpResponse("Invalid password.", status=400)

    if request.method == 'POST':
        event = request.POST.get('event', None)

        # if this is an SMS event
        if event in ['mo_sms', 'mt_sent', 'mt_dlvd']:
            form = TextItSMSForm(request.POST)

            # raise an exception if this doesn't look like a valid request to us
            if not form.is_valid():
                errors = "\n".join("%s: %s" % (_, ",".join(form.errors[_])) for _ in form.errors.keys())
                return HttpResponse("Invalid form, cannot process.\n\nErrors:\n\n%s" % errors, status=400)

            data = form.cleaned_data
            event = data['event']

            # this is an incoming message, handle it
            if event == 'mo_sms':
                router = get_router()

                # look up the backend for this relayer
                backend = lookup_textit_backend_by_phone(data['relayer_phone'])

                # found this backend?  great, let's handle it
                if backend:
                    message = router.handle_incoming(backend['name'], data['phone'], data['text'])
                    json_response['status'] = "message handled"

                # didn't find it, that's our error, not TextIts, so just say we ignored it
                else:
                    json_response['status'] = "no backend found for relayer_phone '%s', ignoring" % data['relayer_phone']

            # this is a sent report
            elif event == 'mt_sent':
                message = Message.objects.filter(external_id=data['sms'])

                # we only care about messages we actually know about
                if message:
                    message.update(status=SENT)
                    json_response['status'] = "message marked as sent"
                    
                else:
                    json_response['status'] = "unknown message"

            # this is a delivery report
            elif event == 'mt_dlvd':
                message = Message.objects.filter(external_id=data['sms'])

                # we only care about messages we actually know about
                if message:
                    message.update(status=DELIVERED)
                    json_response['status'] = "message marked as delivered"

                else:
                    json_response['status'] = "unknown message"

            # the message did not send, this is a failure
            elif event == 'mt_fail':
                message = Message.objects.filter(external_id=data['sms'])

                # we only care about messages we actually know about
                if message:
                    message.update(status=FAILED)
                    json_response['status'] = "message marked as failed"

                else:
                    json_response['status'] = "unknown message"

        else:
            json_response['status'] = "ignoring event"

    else:
        return HttpResponse("Invalid method, must be POST", status=400)

    # build our response from our JSON
    http_response = HttpResponse(json.dumps(json_response))

    # if we are in DEBUG mode, add the access control header, this lets us develop locally using
    # the TextIt WebHook simulator
    if getattr(settings, 'DEBUG', False):
        http_response['Access-Control-Allow-Origin'] = '*'

    return http_response


class TextItSMSForm(forms.Form):
    """
    Form for any TextIt events that involve SMS
    """
    event = forms.CharField(required=True)
    relayer = forms.IntegerField(required=True)
    relayer_phone = forms.CharField(required=True)
    sms = forms.IntegerField(required=True)
    phone = forms.CharField(required=True)
    text = forms.CharField(required=True)
    status = forms.CharField(required=True)
    direction = forms.CharField(required=True)

    # TextIt format is: 2013-01-21T22:34:00.123
    time = forms.DateTimeField(required=True, input_formats=["%Y-%m-%dT%H:%M:%S.%f"])


TEXTIT_SEND_URL = 'https://api.textit.in/api/v1/sms.json'


def send_textit_message(backend, contacts, message):
    """
    Sends a message to TextIt via its API.  Contacts should be an array of contacts.

    This method will raise Exceptions with prejudice in case of errors.
    """
    # look up the textit router for this backend
    textit_backend = lookup_textit_backend_by_name(backend)
    if not textit_backend:
        raise Exception("Unable to find a TextIt backend with name '%s', check ROUTER_URL in your settings.py" % backend)
    
    # build our request
    payload = dict(text=message, phone=contacts)
    headers = { 'Authorization': 'Token %s' % textit_backend['token'],
                'Content-Type': 'application/json' }

    # send things off, raising an exception if we don't get a 200
    r = requests.post(TEXTIT_SEND_URL, data=json.dumps(payload), headers=headers)
    r.raise_for_status()

    # return the message ids that were created on the TextIt side
    json_response = r.json()
    return json_response.get("sms")
