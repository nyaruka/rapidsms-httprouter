
RapidSMS HTTP Router
====================

Implements http endpoints to allow for the RapidSMS 'routing' process to be done in the HTTP thread.  This is a very rough first cut held together with bailing wire.

Built by Nyaruka: http://www.nyaruka.com/

Caveats
-------

Since this obviously does things differently, some things break.  Specifically the following apps will no longer work and should be removed from your ``INSTALLED_APPS`` and ``RAPIDSMS_TABS``::

      rapidsms.contrib.messagelog
      rapidsms.contrib.httptester
      rapidsms.contrib.ajax

The HTTP Router app replaces most of the functionality provided by these packages.  Specifically all messages passing through the HTTP router will be logged automatically and it provides a web interface for viewing and submitting new messages.

Installation
------------

Put ``rapidsms_httprouter`` in your path, then add it to your ``INSTALLED_APPS`` setting::

    INSTALLED_APPS = [
      "rapidsms",
      ..
      ..
      "rapidsms_httprouter"
    ]

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

Messages can be handled and put through the router process using the URL, the result is json::
    
    /router/receive?backend=<backend name>&sender=<sender number>&message=<message text>


Outbox
~~~~~~~

You can see any pending messages which need to be sent using the URL, the result is json::

    /router/outbox


Delivered
~~~~~~~~~~

You can mark a message as sent, or delivered usign the URL::

    /router/delivered?message_id=<message id>








