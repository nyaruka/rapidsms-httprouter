#!/usr/bin/env python
# vim: ai ts=4 sts=4 et sw=4

from django.conf.urls.defaults import *
from .views import receive, outbox, delivered, console
from django.contrib.auth.decorators import login_required

urlpatterns = patterns("",
   ("^router/receive", receive),
   ("^router/outbox", outbox),
   ("^router/delivered", delivered),
   ("^router/console", login_required(console), {}, 'httprouter-console')
)
