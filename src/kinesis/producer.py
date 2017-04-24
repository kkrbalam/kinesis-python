import atexit
import logging
import multiprocessing
import Queue
import signal
import sys
import time

import boto3

log = logging.getLogger(__name__)


class AsyncProducer(object):
    """Async accumulator and producer based on a multiprocessing Queue"""
    MAX_SIZE = 2 ** 20

    def __init__(self, stream_name, buffer_time, queue, boto3_session=None):
        self.stream_name = stream_name
        self.buffer_time = buffer_time
        self.queue = queue
        self.records = []
        self.next_records = []
        self.alive = True

        if boto3_session is None:
            boto3_session = boto3.Session()
        self.client = boto3_session.client('kinesis')

        self.process = multiprocessing.Process(target=self.run)
        self.process.start()

        atexit.register(self.shutdown)

    def shutdown(self):
        self.process.terminate()
        self.process.join()

    def signal_handler(self, signum, frame):
        log.info("Caught signal %s", signum)
        self.alive = False

    def run(self):
        signal.signal(signal.SIGTERM, self.signal_handler)

        try:
            while self.alive or not self.queue.empty():
                records_size = 0
                timer_start = time.time()

                while (time.time() - timer_start < self.buffer_time):
                    try:
                        data = self.queue.get(block=True, timeout=0.1)
                    except Queue.Empty:
                        continue

                    record = {
                        'Data': data,
                        'PartitionKey': '{0}{1}'.format(time.clock(), time.time()),
                    }

                    records_size += sys.getsizeof(record)
                    if records_size >= self.MAX_SIZE:
                        self.next_records = [record]
                        break

                    self.records.append(record)

                self.flush_records()
        except (SystemExit, KeyboardInterrupt):
            pass
        finally:
            self.flush_records()

    def flush_records(self):
        if self.records:
            self.client.put_records(
                StreamName=self.stream_name,
                Records=self.records
            )

        self.records = self.next_records
        self.next_records = []


class KinesisProducer(object):
    """Produce to Kinesis streams via an AsyncProducer"""
    def __init__(self, stream_name, buffer_time=0.5, boto3_session=None):
        self.queue = multiprocessing.Queue()
        self.async_producer = AsyncProducer(stream_name, buffer_time, self.queue, boto3_session=boto3_session)

    def put(self, data):
        self.queue.put(data)
