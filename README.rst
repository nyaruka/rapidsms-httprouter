
RapidSMS HTTP Router
====================

aImplements http endpoints to allow for the RapidSMS 'routing' process to be done in the HTTP thread.  This is a very rough first cut held together with bailing wire.

Installation
------------

Put ``rapidsms_httprouter`` in your path, then add it to your ``INSTALLED_APPS`` setting::

    INSTALLED_APPS = [
      "rapidsms",
      ..
      ..
      "rapidsms_httprouter"
    ]

Note that because of how it works, the HTTP router is /not/ compatibile with either the ajax or httptester apps, you will need to remove these.  It also supercedes the models in ``messaging`` and provides its own ``messagelog`` functionality, so you may want to remove those apps as well.

If you want to use the included console and http tester, add it as a tab::

   RAPIDSMS_TABS = [
     ..
     ("rapidsms_httprouter.views.console",                   "Console"),
   ]

Usage
-----

If you installed the tab, you should be able to click on the Console tab and immediately begin sending messages.  Note that you do not need to run the router process for this to work, instead the HTTP backend detects that there is no router and queues the messages for sending later. (the 'Q' status represents this)

Endpoints
---------

The HTTP router provides the following endpoints:

Receive
~~~~~~~~

Messages can be handled and put through the router process using the URL, the result is json:
    
    /router/receive?backend=<backend name>&sender=<sender number>&message=<message text>


Outbox
~~~~~~~

You can see any pending messages which need to be sent using the URL, the result is json:

    /router/outbox


Delivered
~~~~~~~~~~

You can mark a message as sent, or delivered usign the URL:

    /router/delivered?message_id=<message id>








