APP_STYLE = """
/* A quiet, paper-inspired palette keeps long archive sessions comfortable. */
QWidget {
    color: #29302f;
    font-family: "Inter", "SF Pro Text", "Segoe UI", sans-serif;
    font-size: 14px;
}
QMainWindow, QDialog { background: #f3f1ec; }
QToolTip { color: #f9f7f2; background: #263332; border: 1px solid #435150; padding: 6px 8px; }

QWidget#sidebar { background: #243332; border: 0; }
QLabel#brandTitle { color: #fffaf0; font-family: "Georgia", "Times New Roman", serif; font-size: 22px; font-weight: 700; letter-spacing: 2px; }
QLabel#brandSubtitle { color: #aebebb; font-size: 12px; }
QPushButton[nav="true"] { min-height: 42px; padding: 3px 14px; color: #dce5e2; text-align: left; border: 0; border-radius: 8px; background: transparent; font-weight: 500; }
QPushButton[nav="true"]:hover { background: #314542; color: white; }
QPushButton[nav="true"]:checked { background: #f4eee4; color: #263b38; font-weight: 700; }

QLabel[heading="true"] { color: #263331; font-family: "Georgia", "Times New Roman", serif; font-size: 29px; font-weight: 700; }
QLabel[subheading="true"] { color: #33423f; font-family: "Georgia", "Times New Roman", serif; font-size: 20px; font-weight: 600; }
QLabel[muted="true"] { color: #697472; }
QLabel[emptyState="true"] { color: #77817f; font-family: "Georgia", "Times New Roman", serif; font-size: 18px; padding: 50px; }
QLabel[statValue="true"] { color: #285e55; font-family: "Georgia", "Times New Roman", serif; font-size: 32px; font-weight: 700; }
QFrame#statCard, QFrame#statisticsPanel { background: #fbfaf7; border: 1px solid #d6d8d3; border-radius: 10px; }
QListWidget[statisticsList="true"] { border: 0; background: transparent; }
QListWidget[statisticsList="true"]::item { padding: 9px 5px; }

QPushButton { min-height: 38px; padding: 3px 15px; color: #32403e; background: #fbfaf7; border: 1px solid #c9cfcb; border-radius: 8px; font-weight: 500; }
QPushButton:hover { background: #f5f1e9; border-color: #839791; }
QPushButton:pressed { background: #e9e4da; }
QPushButton:focus { border: 2px solid #56877d; padding: 2px 14px; }
QPushButton:disabled { color: #9ba3a1; background: #ecebe7; border-color: #d9dbd8; }
QPushButton[primary="true"], QPushButton:default { color: white; background: #346b62; border-color: #346b62; font-weight: 700; }
QPushButton[primary="true"]:hover, QPushButton:default:hover { background: #2b5d55; border-color: #2b5d55; }
QPushButton[danger="true"] { color: #a04335; border-color: #d9b7b0; }
QPushButton[danger="true"]:hover { color: white; background: #a94c3d; border-color: #a94c3d; }
QPushButton:checked { color: white; background: #557a73; border-color: #557a73; }

QLineEdit, QComboBox, QSpinBox, QPlainTextEdit { min-height: 38px; padding: 3px 10px; color: #29302f; background: #fffefa; border: 1px solid #c8ceca; border-radius: 8px; selection-background-color: #b9d5ce; }
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QPlainTextEdit:hover { border-color: #94a39f; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus { border: 2px solid #4d8076; padding: 2px 9px; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QPlainTextEdit:disabled { background: #e9e8e4; color: #929a98; }
QComboBox QAbstractItemView { padding: 5px; background: #fffefa; border: 1px solid #aebbb7; selection-background-color: #d7e6e1; selection-color: #243330; outline: 0; }

QListWidget, QTableWidget { background: #fbfaf7; border: 1px solid #d6d8d3; border-radius: 10px; outline: 0; }
QListWidget::item { padding: 9px; border-bottom: 1px solid #ebe9e3; }
QListWidget::item:hover { background: #f2efe7; }
QListWidget::item:selected { color: #233a36; background: #dcebe6; border-bottom-color: #c9ded7; }
QListWidget[bookList="true"]::item { padding: 11px; }

QScrollArea#readerScroll { background: #d9d8d3; border: 0; border-radius: 10px; }
QWidget#readerCanvas { background: #d9d8d3; }
QListWidget[thumbnailList="true"] { background: #ebe9e3; }

QCheckBox { spacing: 8px; }

QProgressBar { min-height: 25px; color: #34413f; background: #e4e4df; border: 0; border-radius: 7px; text-align: center; }
QProgressBar::chunk { background: #5f8e83; border-radius: 7px; }

QScrollBar:vertical { width: 12px; margin: 3px; background: transparent; }
QScrollBar::handle:vertical { min-height: 28px; background: #bdc4c0; border-radius: 5px; }
QScrollBar::handle:vertical:hover { background: #98a49f; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { height: 12px; margin: 3px; background: transparent; }
QScrollBar::handle:horizontal { min-width: 28px; background: #bdc4c0; border-radius: 5px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

QSplitter::handle { background: #d8dad6; }
QSplitter::handle:horizontal { width: 1px; }
QSlider::groove:horizontal { height: 5px; background: #d0d5d1; border-radius: 2px; }
QSlider::handle:horizontal { width: 17px; margin: -6px 0; background: #346b62; border-radius: 8px; }
"""
