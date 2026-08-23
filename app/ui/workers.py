from __future__ import annotations

import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    progress = Signal(int, str)
    succeeded = Signal(object)
    failed = Signal(str, str)
    finished = Signal()


class Worker(QRunnable):
    def __init__(self, operation: Callable[[Callable[[int, str], None]], Any]):
        super().__init__()
        self.operation = operation
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.operation(self.signals.progress.emit)
        except Exception as error:
            self.signals.failed.emit(str(error), traceback.format_exc())
        else:
            self.signals.succeeded.emit(result)
        finally:
            self.signals.finished.emit()

