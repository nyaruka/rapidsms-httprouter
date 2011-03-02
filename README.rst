RapidSMS HTTP Router
====================

Implements HTTP endpoints to allow for the RapidSMS 'routing' process to be done in the HTTP thread.

**Distinct features**

- All message handling is done in the Django HTTP thread
- Easy 'AJAX' endpoints for receiving messages, marking messages as delivered and getting the current outbox
- Supports either queueing messages in the DB to be sent off by an outside process (pull) or sending messages to a configured URL. (push)
- Includes a simple HTTP console web application that integrates with RapidSMS
- "play alike" implementation with RapidSMS, no changes needed to existing applications
- Easy and reliable standalone integration with Kannel

The official source code repository is:
  http://www.github.com/nyaruka/rapidsms-httprouter

Built by Nyaruka Ltd:
  http://www.nyaruka.com

Contributions by UNICEF Uganda:
  http://www.unicef.org

Caveats
-------

Since this obviously does things differently, some things break.  Specifically the following apps will no longer work and should be removed from your ``INSTALLED_APPS`` and ``RAPIDSMS_TABS``::

      rapidsms.contrib.messagelog
      rapidsms.contrib.httptester
      rapidsms.contrib.ajax

The HTTP Router app replaces most of the functionality provided by these packages.  Specifically all messages passing through the HTTP router will be logged automatically and it provides a web interface for viewing and submitting new messages.

Installation From Cheese Shop
=============================

You can install the latest version of the rapidsms-xforms library straight from the cheese shop::

   % pip install rapidsms-httprouter

Installation From Github
========================

You can always get the latest version of rapidsms-httprouter from github.  Note that the tip of the repo is not guaranteed to be stable.  You should use the verison available via pip unless you have a specific reason not to.

You can install the requirements using the ``pip-requires.txt`` file::

   % pip install -r pip-requires.txt

Configuration
=============

Put ``rapidsms_httprouter`` in your path, then add it to your ``INSTALLED_APPS`` setting::

    INSTALLED_APPS = [
      "rapidsms",
      ..
      ..
      "rapidsms_httprouter"
    ]

And add it to your project's urls.py::

   urlpatterns = patterns('',
      .. other url patterns ..
      ('', include('rapidsms_httprouter.urls'))
   )

rapidsms-httprouter also only pulls in the applications you specify for SMS handling.  This lets you use the models from an existing SMS application.  So you'll need to add an ``SMS_APPS`` list to your settings.py::

    SMS_APPS = [
        "mysms.coolapp",
    ]

Finally, if you want to have the router push messages to a specific URL when they are sent, you need to specify that in the settings.py as well::

    ROUTER_URL = "http://backend.server.com/send?backend=%(backend)s&recipient=%(recipient)s&text=%(text)s"

The following fields will be substituted into that string and the resulting URL will then be called via an HTTP GET::

    'backend': the backend that is sending this message
    'recipient': the phone number to send to
    'text': the text to send 
    'id': the internal rapidsms id for this message

If you want to use the included console and http tester, add it as a tab::

   RAPIDSMS_TABS = [
     ..
     ("httprouter-console", "Console"),
   ]

Usage
=====

If you installed the tab, you should be able to click on the Console tab and immediately begin sending messages.  Note that you do not need to run the router process for this to work, instead the HTTP backend detects that there is no router and queues the messages for sending later. (the 'Q' status represents this)

In your app.py, httprouter-aware applications can make use of an extra attribute that will be added to the IncomingMessage object, 'db_message'.  This is a reference to the actual database-persisted message::

    def handle (self, message):
        if hasattr(message, 'db_message'):
           # do something cool, like add the message as a foreign key
           # to one of your app's models, so you know where the model
           # originated

Endpoints
=========

The HTTP router provides the following endpoints:

Receive
-------

Messages can be handled and put through the router process using the URL, the result is json::
    
    /router/receive?backend=<backend name>&sender=<sender number>&message=<message text>


Outbox
------

You can see any pending messages which need to be sent using the URL, the result is json::

    /router/outbox


Delivered
---------

You can mark a message as sent, or delivered usign the URL::

    /router/delivered?message_id=<message id>

Kannel Integration
==================

RapidSMS-HttpRouter works especially well when used with a standalone Kannel configuration.  You just need to configure it to send messages in the format Kannel expects and vice versa.

In your settings.py set your ROUTER_URL like so, adjusting appropriately based on your Kannel configuration::

   ROUTER_URL = "http://localhost:13013/cgi-bin/sendsms?from=123&username=kannel&password=kannel&text=%(text)s&to=%(recipient)s&smsc=%(backend)s&dlr_url=http%%3A%%2F%%2Fmyrapid.com%%2Frouter%%2Fdelivered%%2F%%3Fmessage_id%%3D%(id)s"

The important thing to note here is the dlr_url parameter, which while optional, lets you get delivery reports and mark messages as not just sent but actually delivered according to the SMSC.

A basic Kannel sms-service configuration that would work for this might be::

  group = sms-service
  keyword = default
  max-messages = 0 
  get-url = "http://myrapid.com/router/receive/?backend=%i&sender=%p&message=%b"
  allowed-receiver-prefix = 123;+123
  concatenation = true
  assume-plain-text = true
  accept-x-kannel-headers = true
  omit-empty = true

Multiple Backends
=================

Note that if you have multiple backends, you can set your ROUTER_URL setting to map between backend names and URLs, ie::

    ROUTER_URL = {
        'tigo': 'http://kannel.tigo.com/cgi-bin/sendsms?from=123&username=kannel&password=kannel&text=%(text)s&to=%(recipient)s&smsc=%(backend)s',
	'default': 'http://kannel.mtn.com/cgi-bin/sendsms?from=123&username=kannel&password=kannel&text=%(text)s&to=%(recipient)s&smsc=%(backend)s',
    }

Note that you must either have one entry per backend, or include a 'default' element, which will be used whenever there is not a specific match.

Security
========

It is a good idea to have some security on who can deliver messages to your system, who can see the outbox and who can can mark messages as delivered.  You can lock these down in a rudimentary fashion by settings the ROUTER_PASSWORD attribute in your settings.py::

   ROUTER_PASSWORD = "landshark"

Any incoming requests to those endpoints will fail if it is not included.



