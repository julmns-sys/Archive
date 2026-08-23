from __future__ import annotations

import logging
import os
import sys

# OpenCV's bundled BLAS otherwise starts many native threads before the first
# editor is opened. One thread is sufficient for our single-page operations and
# makes Qt/Python shutdown substantially more predictable.
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from app.config import AppPaths
from app import __version__
from app.database import ArchiveRepository, Database
from app.database.schema import initialize_schema
from app.pdf import PdfService
from app.services import BackupService, LibraryService, UpdateService
from app.ui.main_window import MainWindow
from app.ui.style import APP_STYLE
from app.utilities.logging_setup import configure_logging


def build_application() -> tuple[QApplication, MainWindow]:
    QCoreApplication.setOrganizationName("Bob Archive")
    QCoreApplication.setApplicationName("Bob Archive")
    application = QApplication.instance() or QApplication(sys.argv)
    application.setStyle("Fusion")
    application.setStyleSheet(APP_STYLE)
    paths = AppPaths.discover()
    paths.create()
    configure_logging(paths.log_file)
    database = Database(paths.database_file)
    initialize_schema(database)
    repository = ArchiveRepository(database)
    pdf = PdfService()
    library = LibraryService(paths, repository, pdf)
    backup = BackupService(paths, repository, library)
    updater = UpdateService(paths.root / "updates", __version__)
    return application, MainWindow(repository, library, pdf, backup, updater)


def main() -> int:
    try:
        application, window = build_application()
        window.show()
        return application.exec()
    except Exception as error:
        logging.exception("Application startup failed")
        existing = QApplication.instance()
        if existing:
            QMessageBox.critical(None, "Bob Archive could not start", f"The application could not start.\n\n{error}")
        else:
            print(f"Bob Archive could not start: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
