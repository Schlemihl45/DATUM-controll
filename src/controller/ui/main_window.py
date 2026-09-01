"""
ui/main_window.py — Application shell: StatusBar + NavBar + empty stack
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QStackedWidget,
    QPushButton, QGridLayout,
)

from src.controller.core.machine.controller import MachineController
from src.controller.ui.widgets.status_bar import StatusBar
from src.controller.ui.widgets.machine_info_cards import (
    AxisPositionCard, FeedrateCard, SpindleCard,
)
from src.controller.ui.widgets.card_button import CardButton
from src.controller.ui.icon_loader import get_icon
from src.controller.ui.pages.machine_page import MachinePage
class MainWindow(QMainWindow):

    def __init__(self, controller: MachineController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller

        self.setWindowTitle("DATUM Control")
        self.resize(600, 900)
        self.setWindowIcon(get_icon("logo"))

        self.stack_height = 0

        # -------------------------------------------------------
        # Status bar
        #--------------------------------------------------------
        status_bar = StatusBar(controller, self)

        # -------------------------------------------------------
        # Machine Info Panel
        # -------------------------------------------------------
        machine_info_area = QGridLayout()
        machine_info_area.setContentsMargins(0, 0, 0, 0)
        machine_info_area.setSpacing(6)
        machine_info_area.addWidget(AxisPositionCard(controller), 0,0, 2,1)
        machine_info_area.addWidget(FeedrateCard(controller), 0,1)
        machine_info_area.addWidget(SpindleCard(controller), 1,1)


        # -------------------------------------------------------
        # Stacked Widget
        # -------------------------------------------------------
        self._stack = QStackedWidget(self)

        home_page = QWidget()
        home_grid = QGridLayout(home_page)
        home_grid.setContentsMargins(0, 0, 0, 0)
        home_grid.setSpacing(6)

        # icons
        machine_page_btn = CardButton(icon=get_icon("machine", size=QSize(256,256)), icon_size=256)
        tools_page_btn = CardButton(icon=get_icon("tools", size=QSize(256,256)), icon_size=256)
        setup_page_btn = CardButton(icon=get_icon("setup", size=QSize(256, 256)), icon_size=256)
        programs_page_btn = CardButton(icon=get_icon("workpieces", size=QSize(256,256)), icon_size=256)
        statistics_page_btn = CardButton(icon=get_icon("statistics", size=QSize(256,256)), icon_size=256)
        settings_page_btn = CardButton(icon=get_icon("settings", size=QSize(256,256)), icon_size=256)

        home_grid.addWidget(machine_page_btn, 0, 0)
        home_grid.addWidget(tools_page_btn, 0, 1)
        home_grid.addWidget(setup_page_btn, 1, 0)
        home_grid.addWidget(programs_page_btn, 1, 1)
        home_grid.addWidget(statistics_page_btn, 2, 0)
        home_grid.addWidget(settings_page_btn, 2, 1)

        machine_page_btn.clicked.connect(self._on_machine_btn_clicked)

        self._stack.addWidget(home_page)
        self._stack.addWidget(MachinePage(controller, self))

        # -------------------------------------------------------
        # Quick Button Row
        # -------------------------------------------------------
        button_row = QHBoxLayout()
        button_row.setSpacing(6)

        self.light_btn = CardButton(icon=get_icon("light_off", size=QSize(48, 48)), icon_size=48)
        self.light_btn.setCheckable(True)
        self.light_btn.toggled.connect(self.on_light_toggled)
        self.light_btn.setFixedSize(100, 100)
        button_row.addWidget(self.light_btn)

        self.coolant_btn = CardButton(icon=get_icon("coolant_off"), icon_size=64)
        self.coolant_btn.setCheckable(True)
        self.coolant_btn.toggled.connect(self.on_coolant_toggled)
        self.coolant_btn.setFixedSize(100, 100)
        button_row.addWidget(self.coolant_btn)

        button_row.addStretch(1)

        self.return_btn = CardButton(icon=get_icon("return"), icon_size=48)
        self.return_btn.setFixedSize(100, 100)
        self.return_btn.clicked.connect(self.on_return_clicked)
        button_row.addWidget(self.return_btn)

        # -------------------------------------------------------
        # Layout
        # -------------------------------------------------------
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(6, 6, 6, 6)
        content_layout.setSpacing(6)
        content_layout.addLayout(machine_info_area)
        content_layout.addWidget(self._stack, stretch=1)
        content_layout.addLayout(button_row)

        root_layout = QVBoxLayout()
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(status_bar)
        root_layout.addLayout(content_layout, stretch=1)

        central = QWidget(self)
        central.setObjectName("CentralWidget")
        central.setLayout(root_layout)
        self.setCentralWidget(central)

    def on_return_clicked(self) -> None:
        print(self.stack_height)
        if self.stack_height == 0:
            pass
        elif self.stack_height == 1:
            self._stack.setCurrentIndex(0)

    def on_light_toggled(self, checked: bool) -> None:
        icon = "light_on" if checked else "light_off"
        self.light_btn.set_icon(get_icon(icon, size=QSize(48, 48)))

    def on_coolant_toggled(self, checked: bool) -> None:
        icon = "coolant_on" if checked else "coolant_off"
        self.coolant_btn.set_icon(get_icon(icon, size=QSize(64, 64)))

    def _on_machine_btn_clicked(self) -> None:
        self._stack.setCurrentIndex(1)
        self.stack_height = 1