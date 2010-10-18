import json

from django import forms
from django.http import HttpResponse
from django.template import RequestContext
from django.shortcuts import render_to_response

from rapidsms.messages.incoming import IncomingMessage

from djtables import Table, Column
from djtables.column import DateColumn

from .models import Message
from .router import get_router

class MessageForm(forms.Form):
    backend = forms.CharField(max_length=32)
    sender = forms.CharField(max_length=20)
    message = forms.CharField(max_length=160)

def receive(request):
    """
    Takes the passed in message.  Creates a record for it, and passes it through
    all the rapidsms applications for processing.
    """
    form = MessageForm(request.GET)
    
    # missing fields, fail
    if not form.is_valid():
        return HttpResponse(str(form.errors), status=400)

    # otherwise, create the message
    data = form.cleaned_data

    message = get_router().handle_incoming(data['backend'], data['sender'], data['message'])

    response = {}
    response['message'] = message.as_json()
    response['responses'] = [m.as_json() for m in message.responses.all()]
    response['status'] = "Message handled."

    return HttpResponse(json.dumps(response))

def outbox(request):
    """
    Returns any messages which have been queued to be sent but have no yet been marked
    as being delivered.
    """
    response = {}
    messages = []
    for message in Message.objects.filter(status='Q'):
        messages.append(message.as_json())

    response['outbox'] = messages
    response['status'] = "Outbox follows."

    return HttpResponse(json.dumps(response))

class DeliveredForm(forms.Form):
    message_id = forms.IntegerField()

def delivered(request):
    """
    Called when a message is delivered by our backend.
    """
    form = DeliveredForm(request.GET)
    
    if not form.is_valid():
        return HttpResponse(str(form.errors()), status=400)

    get_router().mark_sent(form.cleaned_data['message_id'])

    return HttpResponse(json.dumps(dict(status="Message marked as sent.")))


class MessageTable(Table):

    # this is temporary, until i fix ModelTable!
    text = Column()
    direction = Column()
    connection = Column()
    status = Column()
    date = DateColumn(format="m/d/Y H:m:s")

    class Meta:
        order_by = '-date'

class SendForm(forms.Form):
    sender = forms.CharField(max_length=20, initial="12065551212")
    text = forms.CharField(max_length=160, label="Message", widget=forms.TextInput(attrs={'size':'100'}))

def console(request):
    """
    Our web console, lets you see recent messages as well as send out new ones for
    processing.
    """

    if request.method == 'GET':
        form = SendForm()
    else:
        form = SendForm(request.POST)
        if form.is_valid():
            backend = "console"
            message = get_router().handle_incoming(backend, 
                                                   form.cleaned_data['sender'],
                                                   form.cleaned_data['text'])

    return render_to_response(
        "router/index.html", {
            "messages_table": MessageTable(Message.objects.all(), request=request),
            "form": form
        }, context_instance=RequestContext(request)
    )

