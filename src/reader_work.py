"""Bound synchronous reader work without blocking HTTP or losing cancelled jobs."""
import asyncio
import contextvars
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from weakref import WeakKeyDictionary, ref


class ReaderWorkPool:
    def __init__(self, limit=8):
        self.limit = limit
        self.executor = ThreadPoolExecutor(max_workers=limit, thread_name_prefix="reader")
        self._gates = WeakKeyDictionary()

    async def run(self, function, *args, **kwargs):
        loop = asyncio.get_running_loop()
        gate_ref = self._gates.get(loop)
        gate = gate_ref() if gate_ref else None
        if gate is None:
            gate = asyncio.Semaphore(self.limit)
            # Semaphores with waiters retain their loop; weak values avoid keeping
            # closed request/test loops alive. Running jobs themselves retain gates.
            self._gates[loop] = ref(gate)
        await gate.acquire()
        try:
            context = contextvars.copy_context()
            worker = self.executor.submit(context.run, partial(function, *args, **kwargs))
        except BaseException:
            gate.release()
            raise

        # Release admission on actual executor completion, never waiter cancellation.
        # A separate callback also retrieves late errors after a request disappears.
        def completed(future):
            if not future.cancelled():
                future.exception()
            try:
                loop.call_soon_threadsafe(gate.release)
            except RuntimeError:
                pass  # The request loop has closed; the worker still cleaned up.

        worker.add_done_callback(completed)
        result = asyncio.wrap_future(worker, loop=loop)
        result.add_done_callback(lambda future: None if future.cancelled() else future.exception())
        return await asyncio.shield(result)


reader_work = ReaderWorkPool()
