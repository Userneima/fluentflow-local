"""Process-local state shared by local-edition job routers.

``JOB_EVENTS`` is THE single event hub for the local edition, and a distinct
instance from the hosted ``server_helpers.JOB_EVENTS``. Local read/cancel
routers already subscribe and cancel on this hub, so when local processing and
the composition root are split they MUST start, publish, subscribe, and cancel
tasks on this same object. Otherwise in-progress tasks return a terminal state
from the database but never stream live progress. Prove it with a non-mock
end-to-end test in the local processing unit — a mocked ``subscribe`` will not
catch the disconnect.
"""

from backend.core.job_event_hub import JobEventHub


JOB_EVENTS = JobEventHub()
