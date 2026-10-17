"""Stand-in workers for ``diarization_process`` tests. A module of their own so
the spawned child can import them by name."""

import os
import time


def hangs(out_queue, audio_path, pid_file=None):
    with open(audio_path + ".pid", "w") as handle:
        handle.write(str(os.getpid()))
    time.sleep(600)


def answers(out_queue, audio_path):
    out_queue.put({"type": "result", "turns": [(0.0, 1.5, "SPEAKER_00"), (1.5, 3.0, "SPEAKER_01")]})


def fails(out_queue, audio_path):
    out_queue.put({"type": "error", "error": "模型文件缺了"})


def dies(out_queue, audio_path):
    os._exit(3)
