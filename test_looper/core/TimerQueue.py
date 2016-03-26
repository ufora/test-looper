"""TimerQueue

A 'Timer'-like abstraction wrapping a single thread and a work queue,
and executes the enqueued work items on a single thread.

"""
import logging
import time
import threading
import test_looper.core.ManagedThread as ManagedThread

class TimerQueue(object):
    def __init__(self, threads = 1):
        self.lock_ = threading.Lock()
        self.condition_ = threading.Condition(self.lock_)
        self.queue_ = []
        self.unsorted_ = False
        self.nextTime_ = None

        # Create `threads` worker threads
        self.threads_ = []
        self.threadsShouldExit = False

        def run():
            while not self.threadsShouldExit:
                self.executeNextWorkItem()

        for x in range(threads):
            t = ManagedThread.ManagedThread(target = run)
            self.threads_.append(t)
            t.start()

    def enqueueWorkItem(self, workItem, args=[], executeAfterDelay=0):
        """ Enqueue a work item to execute after a given delay
        """
        executeAfterTime = time.time() + executeAfterDelay
        with self.lock_:
            self.queue_.append((executeAfterTime, (workItem, args)))
            if self.nextTime_ and self.queue_[1][0] > executeAfterTime:
                # We appended an item that belongs in the middle of the list
                self.unsorted_ = True
            if not self.nextTime_ or executeAfterTime < self.nextTime_:
                self.nextTime_ = executeAfterTime
                self.condition_.notifyAll()

    def executeNextWorkItem(self):
        """ Execute the next scheduled work item, blocking until it's ready
        """
        nextItem = None
        with self.lock_:
            while nextItem is None and not self.threadsShouldExit:
                if self.unsorted_:
                    self.queue_.sort()
                    self.unsorted_ = False

                currentTime = time.time()
                if len(self.queue_) == 0:
                    self.condition_.wait()
                elif self.queue_[0][0] <= currentTime:
                    nextItem = self.queue_[0]
                    self.queue_ = self.queue_[1:]
                    if len(self.queue_) == 0:
                        self.nextTime_ = None
                    else:
                        self.nextTime_ = self.queue_[0][0]
                else:
                    self.condition_.wait(self.nextTime_ - currentTime)

        if nextItem:
            try:
                item = nextItem[1][0]
                args = nextItem[1][1]
                item(*args)
            except:
                import traceback
                logging.error("TimerQueue caught exception from callback: %s", traceback.format_exc())

    def teardown(self):
        self.threadsShouldExit = True

        with self.lock_:
            self.condition_.notifyAll()

        for t in self.threads_:
            t.join()

        self.threadsShouldExit = False
        self.threads_ = []
