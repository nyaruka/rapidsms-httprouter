import datetime

from django.db import models, connections
from django.db.models.query import QuerySet

from rapidsms.models import Contact, Connection

DIRECTION_CHOICES = (
    ('I', "Incoming"),
    ('O', "Outgoing"))

STATUS_CHOICES = (
    ('R', "Received"),
    ('H', "Handled"),

    ('P', "Processing"),
    ('L', "Locked"),

    ('Q', "Queued"),
    ('S', "Sent"),
    ('D', "Delivered"),

    ('C', "Cancelled"),
    ('E', "Errored")
)

class Message(models.Model):
    connection = models.ForeignKey(Connection, related_name='messages')
    text       = models.TextField()

    direction  = models.CharField(max_length=1, choices=DIRECTION_CHOICES)
    status     = models.CharField(max_length=1, choices=STATUS_CHOICES)

    date       = models.DateTimeField(auto_now_add=True)
    updated    = models.DateTimeField(auto_now=True, null=True)

    sent       = models.DateTimeField(null=True, blank=True)
    delivered  = models.DateTimeField(null=True, blank=True)

    in_response_to = models.ForeignKey('self', related_name='responses', null=True, blank=True)

    def __unicode__(self):
        # crop the text (to avoid exploding the admin)
        if len(self.text) < 60: str = self.text
        else: str = "%s..." % (self.text[0:57])

        to_from = (self.direction == "I") and "to" or "from"
        return "%s (%s %s)" % (str, to_from, self.connection.identity)

    def as_json(self):
        return dict(id=self.pk,
                    contact=self.connection.identity, backend=self.connection.backend.name,
                    direction=self.direction, status=self.status, text=self.text,
                    date=self.date.isoformat())

    def send(self):
        """
        Triggers our celery task to send this message off.  Note that our dependency to Celery
        is a soft one, as we only do the import of Tasks here.  If a user has ROUTER_URL
        set to NONE (say when using an Android relayer) then there is no need for Celery and friends.
        """
        from tasks import send_message_task

        # send this message off in celery
        send_message_task.delay(self.pk)

class DeliveryError(models.Model):
    """
    Simple class to keep track of delivery errors for messages.  We retry up to three times before
    finally giving up on sending.
    """
    message = models.ForeignKey(Message, related_name='errors',
                                help_text="The message that had an error")
    log = models.TextField(help_text="A short log on the error that was received when this message was delivered")
    created_on = models.DateTimeField(auto_now_add=True,
                                      help_text="When this delivery error occurred")

