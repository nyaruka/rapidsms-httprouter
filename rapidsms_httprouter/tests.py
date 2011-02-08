"""

Basic unit tests for the HTTP Router.

Not complete by any means, but having something to start with means we can
add issues as they occur so we have automated regression testing.

"""
from django.test import TestCase
from .router import get_router
from .models import Message

from rapidsms.models import Backend, Connection
from rapidsms.apps.base import AppBase
from rapidsms.messages.incoming import IncomingMessage
from rapidsms.messages.outgoing import OutgoingMessage
from django.conf import settings


class RouterTest(TestCase):

    def setUp(self):
        (self.backend, created) = Backend.objects.get_or_create(name="test_backend")
        (self.connection, created) = Connection.objects.get_or_create(backend=self.backend, identity='2067799294')

        # configure with bare minimum to run the http router
        settings.SMS_APPS = []

    def testAddMessage(self):
        router = get_router()

        # tests that messages are correctly build
        msg1 = router.add_message('test', '+250788383383', 'test', 'I', 'P')
        self.assertEquals('test', msg1.connection.backend.name)
        self.assertEquals('250788383383', msg1.connection.identity)
        self.assertEquals('test', msg1.text)
        self.assertEquals('I', msg1.direction)
        self.assertEquals('P', msg1.status)

        # test that connetions are reused and that numbers are normalized
        msg2 = router.add_message('test', '250788383383', 'test', 'I', 'P')
        self.assertEquals(msg2.connection.pk, msg1.connection.pk)

        # test that connections are reused and that numbers are normalized
        msg3 = router.add_message('test', '250-7883-83383', 'test', 'I', 'P')
        self.assertEquals(msg3.connection.pk, msg1.connection.pk)

        # allow letters, maybe shortcodes are using mappings to numbers
        msg4 = router.add_message('test', 'asdfASDF', 'test', 'I', 'P')
        self.assertEquals('asdfasdf', msg4.connection.identity)
        
    def testRouter(self):
        router = get_router()

        msg = OutgoingMessage(self.connection, "test")
        db_msg = router.handle_outgoing(msg)

        # assert a db message was created
        self.assertTrue(db_msg.pk)
        self.assertEqual(db_msg.text, "test")
        self.assertEqual(db_msg.direction, "O")
        self.assertEqual(db_msg.connection, self.connection)
        self.assertEqual(db_msg.status, 'Q')

        # check our queue
        msgs = Message.objects.filter(status='Q')
        self.assertEqual(1, len(msgs))

        # now mark the message as delivered
        router.mark_delivered(db_msg.pk)

        # load it back up
        db_msg = Message.objects.get(id=db_msg.pk)

        # assert it looks ok now
        self.assertEqual(db_msg.text, "test")
        self.assertEqual(db_msg.direction, 'O')
        self.assertEqual(db_msg.connection, self.connection)
        self.assertEqual(db_msg.status, 'D')

    def testAppCancel(self):
        router = get_router()

        class CancelApp(AppBase):
            # cancel outgoing phases by returning True
            def outgoing(self, msg):
                return False

            @property
            def name(self):
                return "ReplyApp"

        try:
            router.apps.append(CancelApp(router))

            msg = OutgoingMessage(self.connection, "test")
            db_msg = router.handle_outgoing(msg)

            # assert a db message was created, but also cancelled
            self.assertTrue(db_msg.pk)
            self.assertEqual(db_msg.text, "test")
            self.assertEqual(db_msg.direction, "O")
            self.assertEqual(db_msg.connection, self.connection)
            self.assertEqual(db_msg.status, 'C')

        finally:
            router.apps = []


    def testAppReply(self):
        router = get_router()

        class ReplyApp(AppBase):
            def handle(self, msg):
                # make sure a db message was given to us
                if not msg.db_message:
                    raise Exception("ReplyApp was not handed a db message")

                # and trigger a reply
                msg.respond("reply")

                # return that we handled it
                return True

            @property
            def name(self):
                return "ReplyApp"

        class ExceptionApp(AppBase):
            def handle(self, msg):
                raise Exception("handle() process was not shortcut by ReplyApp returning True")

        try:
            router.apps.append(ReplyApp(router))
            router.apps.append(ExceptionApp(router))

            db_msg = router.handle_incoming(self.backend.name, self.connection.identity, "test send")

            # assert a db message was created and handled
            self.assertTrue(db_msg.pk)
            self.assertEqual(db_msg.text, "test send")
            self.assertEqual(db_msg.direction, "I")
            self.assertEqual(db_msg.connection, self.connection)
            self.assertEqual(db_msg.status, 'H')

            # assert that a response was associated
            responses = db_msg.responses.all()

            self.assertEqual(1, len(responses))

            response = responses[0]
            self.assertEqual(response.text, "reply")
            self.assertEqual(response.direction, "O")
            self.assertEqual(response.connection, self.connection)
            self.assertEqual(response.status, "Q")

        finally:
            router.apps = []

class ViewTest(TestCase):

    def setUp(self):
        (self.backend, created) = Backend.objects.get_or_create(name="test_backend")
        (self.connection, created) = Connection.objects.get_or_create(backend=self.backend, identity='2067799294')

        # add an echo app
        class EchoApp(AppBase):
            def handle(self, msg):
                msg.respond("echo %s" % msg.text)
                return True

        router = get_router()
        router.apps.append(EchoApp(router))

    def tearDown(self):
        get_router().apps = []


    def testViews(self):
        import json
        
        response = self.client.get("/router/outbox")
        outbox = json.loads(response.content)

        self.assertEquals(0, len(outbox['outbox']))

        # send a message
        response = self.client.get("/router/receive?backend=test_backend&sender=2067799294&message=test")
        message = json.loads(response.content)['message']

        # basic validation that the message was handled
        self.assertEquals("I", message['direction'])
        self.assertEquals("H", message['status'])
        self.assertEquals("test_backend", message['backend'])
        self.assertEquals("2067799294", message['contact'])
        self.assertEquals("test", message['text'])

        # make sure we can load it from the db by its id
        self.assertTrue(Message.objects.get(pk=message['id']))

        # check that the message exists in our outbox
        response = self.client.get("/router/outbox")
        outbox = json.loads(response.content)
        self.assertEquals(1, len(outbox['outbox']))

        # do it again, this checks that getting the outbox is not an action which removes messages
        # from the outbox
        response = self.client.get("/router/outbox")
        outbox = json.loads(response.content)
        self.assertEquals(1, len(outbox['outbox']))

        message = outbox['outbox'][0]
        
        self.assertEquals("O", message['direction'])
        self.assertEquals("Q", message['status'])
        self.assertEquals("test_backend", message['backend'])
        self.assertEquals("2067799294", message['contact'])
        self.assertEquals("echo test", message['text'])

        # mark the message as delivered
        response = self.client.get("/router/delivered?message_id=" + str(message['id']))
        self.assertEquals(200, response.status_code)

        # make sure it has been marked as delivered
        db_message = Message.objects.get(pk=message['id'])
        self.assertEquals('D', db_message.status)

        # and that our outbox is now empty
        response = self.client.get("/router/outbox")
        outbox = json.loads(response.content)

        self.assertEquals(0, len(outbox['outbox']))
                
    

        
    
    
