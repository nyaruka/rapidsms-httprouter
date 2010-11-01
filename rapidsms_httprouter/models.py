import datetime

from django.db import models

from rapidsms.models import Contact, Connection

DIRECTION_CHOICES = (
    ("I", "Incoming"),
    ("O", "Outgoing"))

STATUS_CHOICES = (
    ("R", "Received"),
    ("H", "Handled"),

    ("P", "Processing"),

    ("Q", "Queued"),
    ("S", "Sent"),
    ("D", "Delivered"),

    ("C", "Cancelled"),
    ("E", "Errored")
)

class Message(models.Model):
    connection = models.ForeignKey(Connection, related_name='messages')
    text       = models.TextField()
    direction  = models.CharField(max_length=1, choices=DIRECTION_CHOICES)
    status     = models.CharField(max_length=1, choices=STATUS_CHOICES)
    date       = models.DateTimeField(auto_now_add=True)

    in_response_to = models.ForeignKey('self', related_name='responses', null=True)

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
