from __future__ import absolute_import, print_function, division

import weakref

import gevent
from gevent.event import Event, AsyncResult

import gevent.testing as greentest

from gevent.testing.six import xrange
from gevent.testing.timing import AbstractGenericGetTestCase
from gevent.testing.timing import AbstractGenericWaitTestCase
from gevent.testing.timing import SMALL_TICK
from gevent.testing.timing import SMALL_TICK_MAX_ADJ

DELAY = SMALL_TICK + SMALL_TICK_MAX_ADJ


class TestEventWait(AbstractGenericWaitTestCase):

    def wait(self, timeout):
        Event().wait(timeout=timeout)

    def test_cover(self):
        str(Event())


class TestGeventWaitOnEvent(AbstractGenericWaitTestCase):

    def wait(self, timeout):
        gevent.wait([Event()], timeout=timeout)

    def test_set_during_wait(self):
        # https://github.com/gevent/gevent/issues/771
        # broke in the refactoring. we must not add new links
        # while we're running the callback

        event = Event()

        def setter():
            event.set()

        def waiter():
            s = gevent.spawn(setter)
            # let the setter set() the event;
            # when this method returns we'll be running in the Event._notify_links callback
            # (that is, it switched to us)
            res = event.wait()
            self.assertTrue(res)
            self.assertTrue(event.ready())
            s.join() # make sure it's dead
            # Clear the event. Now we can't wait for the event without
            # another set to happen.
            event.clear()
            self.assertFalse(event.ready())

            # Before the bug fix, this would return "immediately" with
            # event in the result list, because the _notify_links loop would
            # immediately add the waiter and call it
            o = gevent.wait((event,), timeout=0.01)
            self.assertFalse(event.ready())
            self.assertNotIn(event, o)

        gevent.spawn(waiter).join()


class TestAsyncResultWait(AbstractGenericWaitTestCase):

    def wait(self, timeout):
        AsyncResult().wait(timeout=timeout)


class TestWaitAsyncResult(AbstractGenericWaitTestCase):

    def wait(self, timeout):
        gevent.wait([AsyncResult()], timeout=timeout)


class TestAsyncResultGet(AbstractGenericGetTestCase):

    def wait(self, timeout):
        AsyncResult().get(timeout=timeout)

class MyException(Exception):
    pass

class TestAsyncResult(greentest.TestCase):

    def test_link(self):
        ar = AsyncResult()
        self.assertRaises(TypeError, ar.rawlink, None)
        ar.unlink(None) # doesn't raise
        ar.unlink(None) # doesn't raise
        str(ar) # cover

    def test_set_exc(self):
        log = []
        e = AsyncResult()
        self.assertEqual(e.exc_info, ())
        self.assertEqual(e.exception, None)

        def waiter():
            with self.assertRaises(MyException) as exc:
                e.get()
            log.append(('caught', exc.exception))
        gevent.spawn(waiter)
        obj = MyException()
        e.set_exception(obj)
        gevent.sleep(0)
        self.assertEqual(log, [('caught', obj)])

    def test_set(self):
        event1 = AsyncResult()
        timer_exc = MyException('interrupted')

        # Notice that this test is racy:
        # After DELAY, we set the event. We also try to immediately
        # raise the exception with a timer of 0 --- but that depends
        # on cycling the loop. Hence the fairly large value for DELAY.
        g = gevent.spawn_later(DELAY, event1.set, 'hello event1')
        self._close_on_teardown(g.kill)
        with gevent.Timeout.start_new(0, timer_exc):
            with self.assertRaises(MyException) as exc:
                event1.get()
            self.assertIs(timer_exc, exc.exception)

    def test_set_with_timeout(self):
        event2 = AsyncResult()

        X = object()
        result = gevent.with_timeout(DELAY, event2.get, timeout_value=X)
        self.assertIs(
            result, X,
            'Nobody sent anything to event2 yet it received %r' % (result, ))

    def test_nonblocking_get(self):
        ar = AsyncResult()
        self.assertRaises(gevent.Timeout, ar.get, block=False)
        self.assertRaises(gevent.Timeout, ar.get_nowait)


class TestAsyncResultAsLinkTarget(greentest.TestCase):
    error_fatal = False

    def test_set(self):
        g = gevent.spawn(lambda: 1)
        s1, s2, s3 = AsyncResult(), AsyncResult(), AsyncResult()
        g.link(s1)
        g.link_value(s2)
        g.link_exception(s3)
        self.assertEqual(s1.get(), 1)
        self.assertEqual(s2.get(), 1)
        X = object()
        result = gevent.with_timeout(DELAY, s3.get, timeout_value=X)
        self.assertIs(result, X)

    def test_set_exception(self):
        def func():
            raise greentest.ExpectedException('TestAsyncResultAsLinkTarget.test_set_exception')
        g = gevent.spawn(func)
        s1, s2, s3 = AsyncResult(), AsyncResult(), AsyncResult()
        g.link(s1)
        g.link_value(s2)
        g.link_exception(s3)
        self.assertRaises(greentest.ExpectedException, s1.get)
        X = object()
        result = gevent.with_timeout(DELAY, s2.get, timeout_value=X)
        self.assertIs(result, X)
        self.assertRaises(greentest.ExpectedException, s3.get)


class TestEvent_SetThenClear(greentest.TestCase):
    N = 1

    def test(self):
        e = Event()
        waiters = [gevent.spawn(e.wait) for i in range(self.N)]
        gevent.sleep(0.001)
        e.set()
        e.clear()
        for greenlet in waiters:
            greenlet.join()


class TestEvent_SetThenClear100(TestEvent_SetThenClear):
    N = 100


class TestEvent_SetThenClear1000(TestEvent_SetThenClear):
    N = 1000


class TestWait(greentest.TestCase):
    N = 5
    count = None
    timeout = 1
    period = timeout / 100.0

    def _sender(self, events, asyncs):
        while events or asyncs:
            gevent.sleep(self.period)
            if events:
                events.pop().set()
            gevent.sleep(self.period)
            if asyncs:
                asyncs.pop().set()

    @greentest.skipOnAppVeyor("Not all results have arrived sometimes due to timer issues")
    def test(self):
        events = [Event() for _ in xrange(self.N)]
        asyncs = [AsyncResult() for _ in xrange(self.N)]
        max_len = len(events) + len(asyncs)
        sender = gevent.spawn(self._sender, events, asyncs)
        results = gevent.wait(events + asyncs, count=self.count, timeout=self.timeout)
        if self.timeout is None:
            expected_len = max_len
        else:
            expected_len = min(max_len, self.timeout / self.period)
        if self.count is None:
            self.assertTrue(sender.ready(), sender)
        else:
            expected_len = min(self.count, expected_len)
            self.assertFalse(sender.ready(), sender)
            sender.kill()
        self.assertEqual(expected_len, len(results), (expected_len, len(results), results))


class TestWait_notimeout(TestWait):
    timeout = None


class TestWait_count1(TestWait):
    count = 1


class TestWait_count2(TestWait):
    count = 2

class TestEventBasics(greentest.TestCase):

    def test_weakref(self):
        # Event objects should allow weakrefs
        e = Event()
        r = weakref.ref(e)
        self.assertIs(e, r())
        del e
        del r

    def test_wait_while_notifying(self):
        # If someone calls wait() on an Event that is
        # ready, and notifying other waiters, that new
        # waiter still runs at the end, but this does not
        # require a trip around the event loop.
        # See https://github.com/gevent/gevent/issues/1520
        event = Event()
        results = []

        def wait_then_append(arg):
            event.wait()
            results.append(arg)

        gevent.spawn(wait_then_append, 1)
        gevent.spawn(wait_then_append, 2)
        gevent.idle()
        self.assertEqual(2, event.linkcount())
        check = gevent.get_hub().loop.check()
        check.start(results.append, 4)
        event.set()
        wait_then_append(3)
        self.assertEqual(results, [1, 2, 3])
        # Note that the check event DID NOT run.
        check.stop()
        check.close()


del AbstractGenericGetTestCase
del AbstractGenericWaitTestCase

if __name__ == '__main__':
    greentest.main()
