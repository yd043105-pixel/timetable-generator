# -*- coding: utf-8 -*-
"""학교 시간표 생성기 — PySide6 GUI (밝고 깔끔한 화이트 테마).

로직(솔버·저장·진단)은 그대로 두고 화면만 새로 구성했다.
"""
import os
import sys
import threading
import traceback

# --windowed(콘솔 없는) 빌드에서 stdout/stderr 가 None 이면 일부 라이브러리가 죽으므로 막는다.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

_LOG_PATH = os.path.join(os.path.expanduser("~"), "시간표생성기_오류기록.txt")


def _log_error(text):
    try:
        import datetime
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write("\n===== " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " =====\n")
            f.write(text + "\n")
    except Exception:
        pass


def _excepthook(t, e, tb):
    _log_error("".join(traceback.format_exception(t, e, tb)))


sys.excepthook = _excepthook

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QColor, QBrush, QAction, QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QLabel, QPushButton,
    QComboBox, QSpinBox, QCheckBox, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QProgressBar, QFileDialog, QMessageBox, QGroupBox,
    QGridLayout, QVBoxLayout, QHBoxLayout, QScrollArea, QFrame, QStatusBar,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QInputDialog,
)

from excel_parser import parse_excel
from scheduler import HybridScheduler
from models import NonClassSlot, TeacherUnavailable, SimilarSubjectGroup
import output
import session_io
import settings_io

try:
    from cpsat_solver import solve_cpsat, solve_cpsat_iterated, cpsat_available
    HAS_CPSAT = cpsat_available()
except Exception:
    HAS_CPSAT = False

DAYS = ["월", "화", "수", "목", "금"]
PERIODS = [1, 2, 3, 4, 5, 6, 7]

PENALTY_RULES = (
    "■ 반드시 지키는 규칙 (하드 — 하나라도 어기면 시간표로 못 씀, 항상 0)\n"
    "  1. 학급 중복 금지 — 한 학급이 같은 요일·교시에 두 수업 불가\n"
    "  2. 교사 중복 금지 — 한 교사가 같은 요일·교시에 두 곳 수업 불가\n"
    "  3. 비수업 시간 준수 — 지정한 (학년·요일·교시)에 그 학년 수업 금지\n"
    "  4. 교사 불가시간 준수 — 교사가 지정한 시간(학년별 가능)에 그 교사 수업 금지\n"
    "  5. 묶음수업 요일 분산 — 같은 묶음의 여러 시간은 서로 다른 요일\n"
    "  6. 같은 과목 같은 날 금지 — 한 학급의 같은 과목은 하루 허용횟수(보통 1회) 이하\n"
    "  7. 2시간 과목 연속 요일 금지 — 주 2시간 과목을 붙은 요일(예: 월·화)에 두지 않음\n\n"
    "■ 최소화하는 규칙 (소프트 — 점수가 낮을수록 좋음, 괄호는 벌점)\n"
    "  · 교사 연속수업 초과(H7, 50): '최대 연속'을 넘는 연강마다\n"
    "  · 교사 하루 시수 초과(S8, 10): 평균+여유를 넘는 시간마다\n"
    "  · 연강 없이 조각남(25): 교사가 하루 3시수 이상인데 붙은 수업이 하나도 없을 때\n"
    "  · 유사과목 같은 날(S9, 5): 지정한 유사과목 그룹이 한 학급에서 같은 날\n"
    "  · [선택] 점심 전후 연속(40): 한 교사가 점심 직전·직후 교시를 연달아 맡을 때\n\n"
    "※ ‘같은 과목 같은 날’과 ‘2시간 과목 연속 요일’은 하드 규칙입니다.\n"
    "   조건이 빡빡하면(비수업·교사불가가 많으면) 해가 없을 수 있으니, 그때는\n"
    "   비수업·교사불가를 조금 줄이거나 시수를 조정하세요."
)

STYLE = """
/* ───── 기본 ───── */
QWidget {
    background: transparent; color: #0f172a;
    font-family: 'Segoe UI','Malgun Gothic','맑은 고딕';
    font-size: 13px;
}
QMainWindow, QDialog { background: #eef1f6; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }

/* ───── 카드(그룹박스) ───── */
QGroupBox {
    border: 1px solid #e2e8f0; border-radius: 14px;
    margin-top: 20px; padding: 18px 14px 14px 14px;
    background: #ffffff;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 12px; padding: 3px 10px;
    color: #1d4ed8; font-weight: 700; font-size: 13px;
    background: #eff6ff; border-radius: 8px;
}

/* ───── 버튼 ───── */
QPushButton {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #3b82f6, stop:1 #2563eb);
    color: #ffffff; border: none; border-radius: 9px;
    padding: 8px 16px; font-weight: 600;
}
QPushButton:hover { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #2f74e8, stop:1 #1d4ed8); }
QPushButton:pressed { background: #1e40af; padding-top: 9px; padding-bottom: 7px; }
QPushButton:disabled { background: #d7dde7; color: #f5f7fb; }
QPushButton[kind="ghost"] {
    background: #ffffff; color: #334155; border: 1px solid #d8dee9;
}
QPushButton[kind="ghost"]:hover { background: #f1f5f9; border-color: #c3ccda; }
QPushButton[kind="ghost"]:pressed { background: #e2e8f0; }
QPushButton[kind="danger"] { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #f05252, stop:1 #ef4444); }
QPushButton[kind="danger"]:hover { background: #dc2626; }

/* ───── 입력 위젯 ───── */
QComboBox, QSpinBox, QLineEdit {
    border: 1px solid #d8dee9; border-radius: 8px;
    padding: 6px 10px; background: #ffffff;
    selection-background-color: #bfdbfe; selection-color: #0f172a;
}
QComboBox:hover, QSpinBox:hover, QLineEdit:hover { border-color: #b6c2d4; }
QComboBox:focus, QSpinBox:focus, QLineEdit:focus { border: 1.4px solid #2563eb; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow {
    image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid #64748b; margin-right: 8px;
}
QComboBox QAbstractItemView {
    background: #ffffff; border: 1px solid #d8dee9; border-radius: 8px;
    selection-background-color: #eff6ff; selection-color: #1d4ed8; padding: 4px;
}
QSpinBox::up-button, QSpinBox::down-button { width: 18px; border: none; background: #f1f5f9; }
QSpinBox::up-button { border-top-right-radius: 7px; }
QSpinBox::down-button { border-bottom-right-radius: 7px; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #e2e8f0; }
QSpinBox::up-arrow {
    image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-bottom: 5px solid #475569;
}
QSpinBox::down-arrow {
    image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid #475569;
}

/* ───── 목록·텍스트 ───── */
QListWidget {
    border: 1px solid #e2e8f0; border-radius: 10px; background: #ffffff; padding: 4px;
}
QListWidget::item { padding: 4px 6px; border-radius: 6px; }
QListWidget::item:selected { background: #eff6ff; color: #1d4ed8; }
QListWidget::item:hover { background: #f8fafc; }
QPlainTextEdit {
    border: 1px solid #e2e8f0; border-radius: 10px; background: #ffffff; padding: 8px;
}

/* ───── 탭 ───── */
QTabWidget::pane {
    border: 1px solid #e2e8f0; border-radius: 12px; top: 6px; background: #f8fafc;
}
QTabBar::tab {
    background: transparent; color: #64748b;
    padding: 9px 20px; margin-right: 6px; margin-bottom: 6px;
    border-radius: 9px; font-weight: 600;
}
QTabBar::tab:hover { background: #e6ebf3; color: #334155; }
QTabBar::tab:selected { background: #2563eb; color: #ffffff; }

/* ───── 표(시간표 편집) ───── */
QTableWidget {
    border: 1px solid #e2e8f0; border-radius: 10px; background: #ffffff;
    gridline-color: #eef1f6; selection-background-color: #dbeafe; selection-color: #0f172a;
}
QHeaderView::section {
    background: #f1f5f9; color: #475569; font-weight: 700;
    border: none; border-right: 1px solid #e2e8f0; border-bottom: 1px solid #e2e8f0;
    padding: 6px;
}
QTableCornerButton::section { background: #f1f5f9; border: none; }

/* ───── 진행바·체크박스 ───── */
QProgressBar {
    border: none; border-radius: 8px; background: #e2e8f0; height: 14px; text-align: center;
}
QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #3b82f6, stop:1 #6366f1); border-radius: 8px; }
QCheckBox { spacing: 6px; }
QCheckBox::indicator {
    width: 17px; height: 17px; border: 1.4px solid #cbd5e1; border-radius: 5px; background: #ffffff;
}
QCheckBox::indicator:hover { border-color: #93b4f5; }
QCheckBox::indicator:checked { background: #2563eb; border-color: #2563eb; image: none; }

/* ───── 스크롤바 ───── */
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #c7cfdc; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #a9b4c6; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #c7cfdc; border-radius: 5px; min-width: 30px; }
QScrollBar::handle:horizontal:hover { background: #a9b4c6; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ───── 상태바·라벨 ───── */
QStatusBar { background: #eef1f6; color: #64748b; }
QLabel[hint="true"] { color: #94a3b8; font-size: 12px; }
QLabel[role="penalty"] { color: #1d4ed8; font-weight: 700; font-size: 14px; }
/* ───── 메뉴바 ───── */
QMenuBar { background: #ffffff; border-bottom: 1px solid #e2e8f0; padding: 3px 6px; }
QMenuBar::item { padding: 6px 12px; border-radius: 6px; color: #334155; }
QMenuBar::item:selected { background: #eff6ff; color: #1d4ed8; }
QMenuBar::item:pressed { background: #dbeafe; }
QMenu { background: #ffffff; border: 1px solid #d8dee9; border-radius: 8px; padding: 5px; }
QMenu::item { padding: 7px 26px 7px 14px; border-radius: 6px; }
QMenu::item:selected { background: #eff6ff; color: #1d4ed8; }
QMenu::separator { height: 1px; background: #e2e8f0; margin: 5px 8px; }
"""


def resource_path(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


class SolverWorker(QThread):
    progress = Signal(str)
    done_ok = Signal(object, object, str)   # state, solution, status
    done_none = Signal(str, str)            # status, 진단텍스트
    failed = Signal(str)

    def __init__(self, data, nc, ua, sim, params, time_limit, warm):
        super().__init__()
        self.data, self.nc, self.ua, self.sim = data, nc, ua, sim
        self.params, self.time_limit, self.warm = params, time_limit, warm
        self.stop_event = threading.Event()

    def run(self):
        try:
            sch = HybridScheduler(self.data, self.nc, self.ua, self.sim, self.params)
            st, status = solve_cpsat_iterated(sch, time_limit=self.time_limit,
                                     progress=lambda m: self.progress.emit(m),
                                     stop_event=self.stop_event, warm_units=self.warm)
            if st is None:
                try:
                    report = sch.feasibility_text()
                except Exception:
                    report = ""
                self.done_none.emit(status, report)
                return
            sch.polish_pairing(st, rounds=6)
            self.done_ok.emit(st, st.get_solution(), status)
        except Exception:
            tb = traceback.format_exc()
            _log_error(tb)
            self.failed.emit(tb)


class NoWheelSpinBox(QSpinBox):
    """마우스 휠로 값이 바뀌지 않는 스핀박스 (키보드 입력·화살표만)."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, e):
        e.ignore()


class EditTable(QTableWidget):
    """엔터로 수업을 집고 놓는 시간표 격자."""
    def __init__(self, owner):
        super().__init__(len(PERIODS), len(DAYS))
        self.owner = owner
        self.setHorizontalHeaderLabels(DAYS)
        self.setVerticalHeaderLabels([f"{p}교시" for p in PERIODS])
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.verticalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.cellDoubleClicked.connect(lambda r, c: self.owner.on_cell_activate(r, c))

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            r, c = self.currentRow(), self.currentColumn()
            if r >= 0 and c >= 0:
                self.owner.on_cell_activate(r, c)
            return
        if e.key() == Qt.Key_Escape:
            self.owner.cancel_pick()
            return
        super().keyPressEvent(e)


def _btn(text, slot, kind=None):
    b = QPushButton(text)
    b.clicked.connect(slot)
    if kind:
        b.setProperty("kind", kind)
    return b


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("학교 시간표 생성기")
        self.data = None
        self.non_class = []
        self.unavail = []
        self.similar = []
        self.roles = []   # (교사, 역할) 목록
        self.subj_checks = []     # (과목명, QCheckBox)
        self.grid_checks = {}     # (day,period) -> QCheckBox
        self.row_all = {}         # day -> QCheckBox
        self._grid_updating = False
        self.result_sol = None
        self.result_state = None
        self.warm_units = None
        self.worker = None
        self.picked_uid = None       # 편집기에서 집은 수업
        self.edit_cid = None         # 현재 보고 있는 학급

        tabs = QTabWidget()
        tabs.addTab(self._build_input_tab(), "입력 / 생성")
        tabs.addTab(self._build_edit_tab(), "시간표 편집")
        tabs.addTab(self._build_rules_tab(), "페널티 규칙")
        self._tabs = tabs
        wrap = QWidget(); wl = QVBoxLayout(wrap)
        wl.setContentsMargins(14, 12, 14, 8); wl.addWidget(tabs)
        self.setCentralWidget(wrap)

        sb = QStatusBar()
        sb.addPermanentWidget(QLabel("made by 여양고 김동욱"))
        self.setStatusBar(sb)
        self._build_menubar()
        if not HAS_CPSAT:
            self._set_status("⚠ ortools 미설치 — build_exe.bat 으로 다시 빌드하세요")

        self.setStyleSheet(STYLE)
        # 화면 크기에 맞춰 초기 크기를 정하고(작은 노트북에서도 다 보이게) 중앙 배치
        self.setMinimumSize(760, 540)
        try:
            ag = QGuiApplication.primaryScreen().availableGeometry()
            w = min(1060, ag.width() - 60)
            h = min(900, ag.height() - 90)
            self.resize(w, h)
            self.move(ag.x() + (ag.width() - w) // 2, ag.y() + (ag.height() - h) // 2)
        except Exception:
            self.resize(1000, 800)

    def _build_menubar(self):
        mb = self.menuBar()
        m_file = mb.addMenu("파일(&F)")
        m_file.addAction(self._act("시수표 열기…", self.load_file))
        m_file.addAction(self._act("시수표 양식 다운로드…", self.download_template))
        m_file.addSeparator()
        m_file.addAction(self._act("설정 저장…", self.save_settings))
        m_file.addAction(self._act("설정 불러오기…", self.load_settings))
        m_file.addSeparator()
        m_file.addAction(self._act("종료", self.close))
        m_run = mb.addMenu("실행(&R)")
        m_run.addAction(self._act("시간표 생성", self.run))
        m_run.addAction(self._act("결과를 엑셀로 저장", self.save_excel))
        m_help = mb.addMenu("도움말(&H)")
        m_help.addAction(self._act("정보", self._show_about))

    def _act(self, text, slot):
        a = QAction(text, self)
        a.triggered.connect(lambda: slot())
        return a

    def _show_about(self):
        self._msg("정보",
                  "학교 시간표 생성기\n\n"
                  "OR-Tools CP-SAT 기반 자동 시간표 편성 도구\n"
                  "· 비수업/교사불가/유사과목/역할 조건 반영\n"
                  "· 생성 후 드래그·교체 편집 및 실시간 규칙 검사\n\n"
                  "made by 여양고 김동욱")

    # ───────── 시간표 편집 탭 ─────────
    def _build_edit_tab(self):
        w = QWidget(); lay = QVBoxLayout(w)
        top = QHBoxLayout()
        top.addWidget(QLabel("보기"))
        self.cb_edit_mode = QComboBox(); self.cb_edit_mode.addItems(["학급별", "교사별"])
        self.cb_edit_mode.currentIndexChanged.connect(self._edit_mode_changed)
        top.addWidget(self.cb_edit_mode)
        self.lb_edit_sel = QLabel("학급")
        top.addWidget(self.lb_edit_sel)
        self.cb_edit_class = QComboBox(); self.cb_edit_class.setMinimumWidth(130)
        self.cb_edit_class.currentIndexChanged.connect(self._edit_sel_changed)
        top.addWidget(self.cb_edit_class)
        tip = QLabel("셀 선택 후 Enter로 집기 → 다른 칸에서 Enter로 놓기/교체 (Esc 취소). 묶음수업은 통째로 이동.")
        tip.setProperty("hint", "true")
        top.addWidget(tip); top.addStretch()
        lay.addLayout(top)

        mid = QHBoxLayout()
        self.edit_table = EditTable(self)
        mid.addWidget(self.edit_table, 3)
        side = QVBoxLayout()
        side.addWidget(QLabel("⚠ 규칙 경고 (전체)"))
        self.lw_warn = QListWidget(); self.lw_warn.setMinimumWidth(280)
        side.addWidget(self.lw_warn)
        self.lb_pick = QLabel("집은 수업: 없음"); self.lb_pick.setProperty("role", "penalty")
        side.addWidget(self.lb_pick)
        side.addWidget(_btn("엑셀로 저장", self.save_excel, "ghost"))
        sw = QWidget(); sw.setLayout(side)
        mid.addWidget(sw, 2)
        lay.addLayout(mid)
        return w

    @property
    def edit_mode(self):
        return "teacher" if self.cb_edit_mode.currentIndex() == 1 else "class"

    def load_editor(self):
        """생성 완료 후 편집기에 시간표를 싣는다."""
        if self.result_state is None or self.data is None:
            return
        self.picked_uid = None
        self.lb_pick.setText("집은 수업: 없음")
        self.data_units = self.result_state.sch.units
        self._populate_selector()
        self._refresh_grid()

    def _populate_selector(self):
        self.cb_edit_class.blockSignals(True)
        self.cb_edit_class.clear()
        if self.edit_mode == "class":
            self.lb_edit_sel.setText("학급")
            items = []
            for g in sorted(self.data.classes_per_grade):
                items.extend(self.data.classes_per_grade[g])
        else:
            self.lb_edit_sel.setText("교사")
            items = list(self.data.teachers)
        self.cb_edit_class.addItems(items)
        self.cb_edit_class.blockSignals(False)
        self.edit_key = items[0] if items else None
        if items:
            self.cb_edit_class.setCurrentIndex(0)

    def _edit_mode_changed(self):
        self.picked_uid = None
        self.lb_pick.setText("집은 수업: 없음")
        self._populate_selector()
        self._refresh_grid()

    def _edit_sel_changed(self):
        self.edit_key = self.cb_edit_class.currentText()
        self.picked_uid = None
        self.lb_pick.setText("집은 수업: 없음")
        self._refresh_grid()

    def _unit_label(self, uid, key, mode):
        unit = self.data_units[uid]
        tag = " ◆묶음" if unit.bundle_key else ""
        if mode == "class":
            for (c, t, s) in unit.cells:
                if c == key:
                    return f"{s}\n{t}{tag}"
            return ""
        else:  # teacher view: 그 교사가 맡는 학급(들) 표시
            parts = [(s, c) for (c, t, s) in unit.cells if t == key]
            if not parts:
                return ""
            subj = parts[0][0]
            cids = ", ".join(c for _, c in parts)
            return f"{subj}\n{cids}{tag}"

    def _occ_for_key(self, st, key, mode, d, p):
        occ_c, occ_t = st.cell_units()
        return (occ_c if mode == "class" else occ_t).get((key, d, p), [])

    def _refresh_grid(self):
        st = self.result_state
        key = getattr(self, "edit_key", None)
        if st is None or not key:
            return
        mode = self.edit_mode
        occ_c, occ_t = st.cell_units()
        occ = occ_c if mode == "class" else occ_t
        conflicts = st.conflict_class_cells() if mode == "class" else st.conflict_teacher_cells()
        safe = set(st.safe_slots_for(self.picked_uid)) if self.picked_uid is not None else set()
        tbl = self.edit_table
        tbl.blockSignals(True)
        for ri, p in enumerate(PERIODS):
            for ci, dname in enumerate(DAYS):
                uids = occ.get((key, dname, p), [])
                text = "\n──\n".join(self._unit_label(u, key, mode) for u in uids) if uids else ""
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter)
                if (key, dname, p) in conflicts:
                    item.setBackground(QBrush(QColor("#fecaca")))      # 빨강 — 중복
                elif self.picked_uid is not None and (dname, p) in safe:
                    item.setBackground(QBrush(QColor("#bbf7d0")))      # 초록 — 안전 위치
                elif self.picked_uid is not None and uids and self.picked_uid in uids:
                    item.setBackground(QBrush(QColor("#fde68a")))      # 노랑 — 집은 수업
                else:
                    item.setBackground(QBrush(QColor("#ffffff")))
                tbl.setItem(ri, ci, item)
        tbl.blockSignals(False)
        self._refresh_warnings()

    def _refresh_warnings(self):
        self.lw_warn.clear()
        msgs = self.result_state.editor_violations()
        if not msgs:
            it = QListWidgetItem("✓ 규칙 위반 없음")
            it.setForeground(QBrush(QColor("#16a34a")))
            self.lw_warn.addItem(it)
        else:
            for m in msgs:
                it = QListWidgetItem(m)
                it.setForeground(QBrush(QColor("#dc2626")))
                self.lw_warn.addItem(it)

    def on_cell_activate(self, row, col):
        st = self.result_state
        key = getattr(self, "edit_key", None)
        if st is None or not key:
            return
        d = DAYS[col]; p = PERIODS[row]
        mode = self.edit_mode
        here = self._occ_for_key(st, key, mode, d, p)
        if self.picked_uid is None:
            if here:
                if len(here) == 1:
                    self.picked_uid = here[0]
                else:
                    labels = [f"{i+1}. " + self._unit_label(u, key, mode).replace("\n", " ")
                              for i, u in enumerate(here)]
                    choice, ok = QInputDialog.getItem(
                        self, "수업 선택",
                        f"{d}요일 {p}교시에 수업이 {len(here)}개 겹쳐 있습니다.\n옮길 수업을 고르세요:",
                        labels, 0, False)
                    if not ok:
                        return
                    self.picked_uid = here[labels.index(choice)]
                lab = self._unit_label(self.picked_uid, key, mode).replace("\n", " ")
                self.lb_pick.setText(f"집은 수업: {lab}  →  놓을 칸에서 Enter")
            self._refresh_grid()
        else:
            A = self.picked_uid
            others = [u for u in here if u != A]
            if others:
                st.swap(A, others[0])
            else:
                st.move(A, d, p)
            self.picked_uid = None
            self.lb_pick.setText("집은 수업: 없음")
            self.result_sol = st.get_solution()
            self._refresh_grid()

    def cancel_pick(self):
        self.picked_uid = None
        self.lb_pick.setText("집은 수업: 없음")
        self._refresh_grid()

    # ───────── 탭 구성 ─────────
    def _build_rules_tab(self):
        w = QWidget(); lay = QVBoxLayout(w)
        t = QPlainTextEdit(); t.setReadOnly(True); t.setPlainText(PENALTY_RULES)
        t.setFont(QFont("Malgun Gothic", 11))
        lay.addWidget(t)
        return w

    def _build_input_tab(self):
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        inner = QWidget(); v = QVBoxLayout(inner); v.setSpacing(10); v.setContentsMargins(12, 12, 12, 12)

        # 1. 기본 정보
        g1 = QGroupBox("1. 기본 정보 · 시수표")
        l1 = QGridLayout(g1)
        l1.addWidget(QLabel("학년도"), 0, 0)
        self.ed_year = QLineEdit("2026"); self.ed_year.setFixedWidth(70); l1.addWidget(self.ed_year, 0, 1)
        l1.addWidget(QLabel("학기"), 0, 2)
        self.cb_sem = QComboBox(); self.cb_sem.addItems(["1", "2"]); l1.addWidget(self.cb_sem, 0, 3)
        l1.addWidget(_btn("시수표 양식 다운로드", self.download_template, "ghost"), 0, 4)
        l1.addWidget(_btn("시수표(.xlsx) 열기", self.load_file), 0, 5)
        self.lb_file = QLabel("파일 없음"); self.lb_file.setProperty("hint", "true")
        l1.addWidget(self.lb_file, 1, 0, 1, 6)
        l1.addWidget(_btn("설정 저장(엑셀)", self.save_settings, "ghost"), 2, 4)
        l1.addWidget(_btn("설정 불러오기(엑셀)", self.load_settings, "ghost"), 2, 5)
        hint1 = QLabel("비수업·교사불가·유사그룹 등 입력 조건을 엑셀로 보관/재적용"); hint1.setProperty("hint", "true")
        l1.addWidget(hint1, 2, 0, 1, 4)
        v.addWidget(g1)

        # 2. 비수업 시간 (학년별)
        g2 = QGroupBox("2. 비수업 시간 (그 학년 전체가 수업 불가)")
        l2 = QVBoxLayout(g2)
        top2 = QHBoxLayout()
        self.cb_nc_day = QComboBox(); self.cb_nc_day.addItems(DAYS)
        self.cb_nc_per = QComboBox(); self.cb_nc_per.addItems([str(p) for p in PERIODS])
        top2.addWidget(QLabel("요일")); top2.addWidget(self.cb_nc_day)
        top2.addWidget(QLabel("교시")); top2.addWidget(self.cb_nc_per)
        top2.addWidget(_btn("1학년 추가", lambda: self.add_nc([1]), "ghost"))
        top2.addWidget(_btn("2학년 추가", lambda: self.add_nc([2]), "ghost"))
        top2.addWidget(_btn("3학년 추가", lambda: self.add_nc([3]), "ghost"))
        top2.addWidget(_btn("전체학년 추가", lambda: self.add_nc([1, 2, 3])))
        top2.addStretch()
        l2.addLayout(top2)
        cols2 = QHBoxLayout()
        self.nc_lists = {}
        for g in [1, 2, 3]:
            box = QVBoxLayout()
            box.addWidget(QLabel(f"{g}학년"))
            lw = QListWidget(); lw.setSelectionMode(QListWidget.ExtendedSelection); lw.setFixedHeight(110)
            box.addWidget(lw)
            box.addWidget(_btn("선택 삭제", lambda _=False, gg=g: self.del_nc(gg), "ghost"))
            self.nc_lists[g] = lw
            cols2.addLayout(box)
        l2.addLayout(cols2)
        v.addWidget(g2)

        # 3. 교사 불가시간 (격자 + 전체열)
        g3 = QGroupBox("3. 교사 불가시간")
        l3 = QVBoxLayout(g3)
        top3 = QHBoxLayout()
        top3.addWidget(QLabel("교사"))
        self.cb_ua_teacher = QComboBox(); self.cb_ua_teacher.setMinimumWidth(140)
        self.cb_ua_teacher.currentIndexChanged.connect(self._on_ua_teacher)
        top3.addWidget(self.cb_ua_teacher)
        top3.addWidget(QLabel("학년"))
        self.cb_ua_grade = QComboBox(); self.cb_ua_grade.addItems(["전체학년", "1학년", "2학년", "3학년"])
        self.cb_ua_grade.currentIndexChanged.connect(self._on_ua_teacher)
        top3.addWidget(self.cb_ua_grade)
        top3.addWidget(_btn("이 교사 불가시간 등록", self.register_ua))
        top3.addWidget(_btn("격자 비우기", self.clear_grid, "ghost"))
        h3 = QLabel("(교사·학년 선택 → 칸 체크 → 등록 / 학년 미선택=전체)"); h3.setProperty("hint", "true")
        top3.addWidget(h3); top3.addStretch()
        l3.addLayout(top3)
        gridw = QWidget(); grid = QGridLayout(gridw); grid.setSpacing(4)
        for j, p in enumerate(PERIODS):
            grid.addWidget(QLabel(f"{p}교시"), 0, j + 1, alignment=Qt.AlignCenter)
        lbl_all = QLabel("전체"); lbl_all.setStyleSheet("color:#2563eb;font-weight:700;")
        grid.addWidget(lbl_all, 0, len(PERIODS) + 1, alignment=Qt.AlignCenter)
        for i, day in enumerate(DAYS):
            grid.addWidget(QLabel(day), i + 1, 0, alignment=Qt.AlignCenter)
            for j, p in enumerate(PERIODS):
                cb = QCheckBox()
                cb.stateChanged.connect(lambda _s, d=day: self._on_cell_changed(d))
                grid.addWidget(cb, i + 1, j + 1, alignment=Qt.AlignCenter)
                self.grid_checks[(day, p)] = cb
            av = QCheckBox()
            av.stateChanged.connect(lambda _s, d=day: self._toggle_row_all(d))
            grid.addWidget(av, i + 1, len(PERIODS) + 1, alignment=Qt.AlignCenter)
            self.row_all[day] = av
        l3.addWidget(gridw)
        lbl_reg = QLabel("등록된 불가시간 (선택 후 삭제):"); lbl_reg.setProperty("hint", "true")
        l3.addWidget(lbl_reg)
        self.lw_ua = QListWidget(); self.lw_ua.setSelectionMode(QListWidget.ExtendedSelection); self.lw_ua.setFixedHeight(80)
        l3.addWidget(self.lw_ua)
        l3.addWidget(_btn("선택 삭제", self.del_ua, "ghost"), alignment=Qt.AlignRight)
        v.addWidget(g3)

        # 4. 유사과목 그룹 (체크박스)
        g4 = QGroupBox("4. 유사과목 그룹 (같은 학급 같은 날 회피)")
        l4 = QGridLayout(g4)
        l4.addWidget(QLabel("과목 체크 →"), 0, 0, alignment=Qt.AlignTop)
        self.subj_area = QScrollArea(); self.subj_area.setWidgetResizable(True); self.subj_area.setFixedHeight(130)
        self.subj_host = QWidget(); self.subj_grid = QGridLayout(self.subj_host)
        self.subj_area.setWidget(self.subj_host)
        l4.addWidget(self.subj_area, 0, 1)
        right4 = QVBoxLayout()
        right4.addWidget(QLabel("그룹 이름"))
        self.ed_grp = QLineEdit(); right4.addWidget(self.ed_grp)
        right4.addWidget(_btn("그룹 추가", self.add_grp))
        right4.addWidget(_btn("선택 삭제", self.del_grp, "ghost"))
        right4.addStretch()
        rw = QWidget(); rw.setLayout(right4); l4.addWidget(rw, 0, 2)
        self.lw_grp = QListWidget(); self.lw_grp.setFixedHeight(76)
        l4.addWidget(self.lw_grp, 1, 0, 1, 3)
        v.addWidget(g4)

        # 5. 역할 지정
        g_role = QGroupBox("5. 역할 지정 (부장·홍보)")
        lr = QVBoxLayout(g_role)
        topr = QHBoxLayout()
        topr.addWidget(QLabel("교사"))
        self.cb_role_teacher = QComboBox(); self.cb_role_teacher.setMinimumWidth(130)
        topr.addWidget(self.cb_role_teacher)
        topr.addWidget(QLabel("역할"))
        self.cb_role_kind = QComboBox()
        self.cb_role_kind.addItems(["교무부장", "학년부장", "홍보담당"])
        topr.addWidget(self.cb_role_kind)
        topr.addWidget(_btn("역할 추가", self.add_role))
        topr.addWidget(_btn("선택 삭제", self.del_role, "ghost"))
        hintr = QLabel("교무부장=1교시 회피+2학기 1~4교시만(하드) · 학년부장=1교시 회피(후순위) · 홍보담당=요일마다 최소 1명 1~4교시만")
        hintr.setProperty("hint", "true")
        topr.addWidget(hintr); topr.addStretch()
        lr.addLayout(topr)
        self.lw_role = QListWidget(); self.lw_role.setFixedHeight(80)
        lr.addWidget(self.lw_role)
        v.addWidget(g_role)

        # 6. 설정
        g5 = QGroupBox("6. 설정")
        l5 = QGridLayout(g5)
        l5.addWidget(QLabel("교사 최대 연속"), 0, 0)
        self.sp_mc = NoWheelSpinBox(); self.sp_mc.setRange(1, 7); self.sp_mc.setValue(2); l5.addWidget(self.sp_mc, 0, 1)
        l5.addWidget(QLabel("하루 시수 여유(+n)"), 0, 2)
        self.sp_dn = NoWheelSpinBox(); self.sp_dn.setRange(0, 5); self.sp_dn.setValue(1); l5.addWidget(self.sp_dn, 0, 3)
        l5.addWidget(QLabel("탐색 시간(초)"), 0, 4)
        self.sp_time = NoWheelSpinBox(); self.sp_time.setRange(10, 3600); self.sp_time.setSingleStep(10); self.sp_time.setValue(90); l5.addWidget(self.sp_time, 0, 5)
        self.ck_lunch = QCheckBox("점심 전후 연속수업 방지"); l5.addWidget(self.ck_lunch, 1, 0, 1, 2)
        l5.addWidget(QLabel("점심 직전 교시"), 1, 2)
        self.sp_lunchp = NoWheelSpinBox(); self.sp_lunchp.setRange(1, 6); self.sp_lunchp.setValue(4); l5.addWidget(self.sp_lunchp, 1, 3)
        v.addWidget(g5)

        # 7. 실행 / 결과
        g6 = QGroupBox("7. 생성")
        l6 = QVBoxLayout(g6)
        row6 = QHBoxLayout()
        self.btn_run = _btn("시간표 생성", self.run)
        self.btn_more = _btn("더 돌리기(이어서)", self.run_more, "ghost"); self.btn_more.setEnabled(False)
        self.btn_stop = _btn("중단", self.stop, "danger"); self.btn_stop.setEnabled(False)
        self.btn_save = _btn("엑셀로 저장", self.save_excel, "ghost"); self.btn_save.setEnabled(False)
        self.btn_sess_save = _btn("이어돌리기 저장", self.save_session, "ghost"); self.btn_sess_save.setEnabled(False)
        self.btn_sess_load = _btn("이어돌리기 열기", self.load_session, "ghost")
        for b in (self.btn_run, self.btn_more, self.btn_stop, self.btn_save, self.btn_sess_save, self.btn_sess_load):
            row6.addWidget(b)
        row6.addStretch()
        l6.addLayout(row6)
        self.pbar = QProgressBar(); self.pbar.setRange(0, 0); self.pbar.hide()
        l6.addWidget(self.pbar)
        self.lb_penalty = QLabel(""); self.lb_penalty.setProperty("role", "penalty")
        l6.addWidget(self.lb_penalty)
        self.txt_result = QPlainTextEdit(); self.txt_result.setReadOnly(True); self.txt_result.setMinimumHeight(200)
        l6.addWidget(self.txt_result)
        v.addWidget(g6)

        v.addStretch()
        scroll.setWidget(inner)
        return scroll

    # ───────── 헬퍼 ─────────
    def _set_status(self, text):
        self.statusBar().showMessage(text)

    def _msg(self, title, text, icon=QMessageBox.Information):
        m = QMessageBox(self); m.setIcon(icon); m.setWindowTitle(title); m.setText(text); m.exec()

    def _collect_params(self):
        first = [t for t, r in self.roles if r in ("교무부장", "학년부장")]
        after3 = [t for t, r in self.roles if r == "홍보담당"]
        gyomu = [t for t, r in self.roles if r == "교무부장"]
        return dict(max_consecutive=self.sp_mc.value(), daily_n=self.sp_dn.value(),
                    lunch_split=self.ck_lunch.isChecked(), lunch_period=self.sp_lunchp.value(),
                    first_avoid_teachers=first, after3_avoid_teachers=after3,
                    gyomu_teachers=gyomu, semester=int(self.cb_sem.currentText()),
                    roles_list=list(self.roles))

    # ───────── 역할 지정 ─────────
    def add_role(self):
        t = self.cb_role_teacher.currentText()
        r = self.cb_role_kind.currentText()
        if not t:
            self._msg("안내", "교사를 먼저 선택하세요."); return
        if (t, r) in self.roles:
            return
        self.roles.append((t, r))
        self._render_roles()

    def del_role(self):
        rows = sorted((self.lw_role.row(it) for it in self.lw_role.selectedItems()), reverse=True)
        for i in rows:
            if 0 <= i < len(self.roles):
                del self.roles[i]
        self._render_roles()

    def _render_roles(self):
        self.lw_role.clear()
        for t, r in self.roles:
            self.lw_role.addItem(f"{t} — {r}")

    # ───────── 파일/설정 ─────────
    def download_template(self):
        src = resource_path("template.xlsx")
        if not os.path.exists(src):
            self._msg("오류", "양식 파일을 찾을 수 없습니다.", QMessageBox.Warning); return
        path, _ = QFileDialog.getSaveFileName(self, "양식 저장", "교사별_시수표_양식.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        try:
            import shutil; shutil.copyfile(src, path)
            self._msg("저장 완료", f"양식을 저장했습니다:\n{path}")
        except Exception as e:
            self._msg("저장 실패", str(e), QMessageBox.Warning)

    def load_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "시수표 열기", "", "Excel (*.xlsx *.xls)")
        if not path:
            return
        try:
            self.data = parse_excel(path)
        except Exception as e:
            self._msg("읽기 실패", str(e), QMessageBox.Warning); return
        self.warm_units = None
        self._after_data_loaded(os.path.basename(path))

    def _after_data_loaded(self, label):
        d = self.data
        ncls = sum(len(v) for v in d.classes_per_grade.values())
        self.lb_file.setText(f"✓ {label} — 학급 {ncls}, 교사 {len(d.teachers)}, 묶음 {len(d.bundle_groups)}")
        self.lb_file.setStyleSheet("color:#16a34a;")
        self.cb_ua_teacher.blockSignals(True)
        self.cb_ua_teacher.clear(); self.cb_ua_teacher.addItems(d.teachers)
        self.cb_ua_teacher.blockSignals(False)
        self.cb_role_teacher.clear(); self.cb_role_teacher.addItems(d.teachers)
        # 유사과목 체크박스 다시 그림
        for i in reversed(range(self.subj_grid.count())):
            w = self.subj_grid.itemAt(i).widget()
            if w:
                w.setParent(None)
        self.subj_checks = []
        for i, s in enumerate(d.subjects):
            cb = QCheckBox(s)
            self.subj_grid.addWidget(cb, i // 3, i % 3)
            self.subj_checks.append((s, cb))

    def save_settings(self):
        self._sync_current_grid()
        path, _ = QFileDialog.getSaveFileName(self, "설정 저장", "시간표설정.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        try:
            params = self._collect_params(); params["time_limit"] = self.sp_time.value()
            settings_io.save_settings_xlsx(path, list(self.non_class), list(self.unavail),
                                           list(self.similar), int(self.ed_year.text()),
                                           int(self.cb_sem.currentText()), params, roles=list(self.roles))
            self._msg("저장 완료",
                      f"입력 설정을 저장했습니다:\n{path}\n\n"
                      f"(비수업 {len(self.non_class)} · 교사불가 {len(self.unavail)} · 유사그룹 {len(self.similar)})\n"
                      "다음에 ‘설정 불러오기’로 적용할 수 있습니다.")
        except Exception as e:
            self._msg("저장 실패", str(e), QMessageBox.Warning)

    def load_settings(self):
        path, _ = QFileDialog.getOpenFileName(self, "설정 불러오기", "", "Excel (*.xlsx *.xls)")
        if not path:
            return
        try:
            r = settings_io.load_settings_xlsx(path)
        except Exception as e:
            self._msg("불러오기 실패", str(e), QMessageBox.Warning); return
        self.non_class = r["non_class"]; self.unavail = r["unavail"]; self.similar = r["similar"]
        self.roles = list(r.get("roles", []))
        self._render_roles()
        s = r.get("settings") or {}
        if s:
            self.ed_year.setText(str(s.get("year", 2026)))
            self.cb_sem.setCurrentText(str(s.get("semester", 1)))
            self.sp_mc.setValue(int(s.get("max_consecutive", 2)))
            self.sp_dn.setValue(int(s.get("daily_n", 1)))
            self.sp_time.setValue(int(s.get("time_limit", 90)))
            self.ck_lunch.setChecked(bool(s.get("lunch_split", False)))
            self.sp_lunchp.setValue(int(s.get("lunch_period", 4)))
        self._refresh_all_lists()
        miss = ""
        if self.data is not None:
            unknown = sorted({u.teacher for u in self.unavail} - set(self.data.teachers))
            if unknown:
                miss = "\n\n※ 현재 시수표에 없는 교사: " + ", ".join(unknown)
        self._msg("불러옴", f"설정 적용: 비수업 {len(self.non_class)} · 교사불가 {len(self.unavail)} · 유사그룹 {len(self.similar)}" + miss)

    def _refresh_all_lists(self):
        for g in (1, 2, 3):
            self.nc_lists[g].clear()
        for s in self.non_class:
            self.nc_lists[s.grade].addItem(f"{s.day} {s.period}교시")
        self._render_ua()
        self.lw_grp.clear()
        for s in self.similar:
            self.lw_grp.addItem(f"{s.name}: {'·'.join(s.subjects)}")

    # ───────── 비수업 ─────────
    def add_nc(self, grades):
        day = self.cb_nc_day.currentText(); period = int(self.cb_nc_per.currentText())
        for g in grades:
            if not any(s.grade == g and s.day == day and s.period == period for s in self.non_class):
                self.non_class.append(NonClassSlot(grade=g, day=day, period=period))
                self.nc_lists[g].addItem(f"{day} {period}교시")

    def del_nc(self, g):
        lw = self.nc_lists[g]
        for it in lw.selectedItems():
            txt = it.text(); day, per = txt.split(" ")
            per = int(per.replace("교시", ""))
            self.non_class = [s for s in self.non_class if not (s.grade == g and s.day == day and s.period == per)]
            lw.takeItem(lw.row(it))

    # ───────── 교사 불가 격자 ─────────
    def _on_cell_changed(self, day):
        if self._grid_updating:
            return
        all_on = all(self.grid_checks[(day, p)].isChecked() for p in PERIODS)
        self._grid_updating = True
        self.row_all[day].setChecked(all_on)
        self._grid_updating = False

    def _toggle_row_all(self, day):
        if self._grid_updating:
            return
        val = self.row_all[day].isChecked()
        self._grid_updating = True
        for p in PERIODS:
            self.grid_checks[(day, p)].setChecked(val)
        self._grid_updating = False

    def _cur_ua_grade(self):
        return self.cb_ua_grade.currentIndex()  # 0=전체, 1·2·3=학년

    def _on_ua_teacher(self):
        t = self.cb_ua_teacher.currentText()
        g = self._cur_ua_grade()
        cur = {(s.day, s.period) for s in self.unavail
               if s.teacher == t and (getattr(s, "grade", 0) or 0) == g}
        self._grid_updating = True
        for (day, p), cb in self.grid_checks.items():
            cb.setChecked((day, p) in cur)
        self._grid_updating = False
        for day in DAYS:
            self._on_cell_changed(day)

    def clear_grid(self):
        self._grid_updating = True
        for cb in self.grid_checks.values():
            cb.setChecked(False)
        for cb in self.row_all.values():
            cb.setChecked(False)
        self._grid_updating = False

    def register_ua(self):
        t = self.cb_ua_teacher.currentText()
        if not t:
            self._msg("안내", "교사를 먼저 선택하세요."); return
        self._sync_current_grid()
        g = self._cur_ua_grade()
        gname = ["전체 학년", "1학년", "2학년", "3학년"][g]
        cnt = sum(1 for s in self.unavail
                  if s.teacher == t and (getattr(s, "grade", 0) or 0) == g)
        self._msg("등록 완료", f"{t} 선생님의 {gname} 불가시간 {cnt}칸을 등록했습니다.")

    def _sync_current_grid(self):
        # 현재 (교사, 선택 학년) 조합의 격자 체크 상태를 self.unavail 에 반영.
        # 다른 학년의 불가시간은 건드리지 않는다.
        t = self.cb_ua_teacher.currentText()
        if not t:
            return
        g = self._cur_ua_grade()
        self.unavail = [s for s in self.unavail
                        if not (s.teacher == t and (getattr(s, "grade", 0) or 0) == g)]
        for (day, p), cb in self.grid_checks.items():
            if cb.isChecked():
                self.unavail.append(TeacherUnavailable(teacher=t, day=day, period=p, grade=g))
        self._render_ua()

    def del_ua(self):
        keep_rows = {self.lw_ua.row(it) for it in self.lw_ua.selectedItems()}
        new = [s for i, s in enumerate(self.unavail) if i not in keep_rows]
        self.unavail = new
        self._render_ua()

    def _render_ua(self):
        self.lw_ua.clear()
        gn = {0: "", 1: " (1학년)", 2: " (2학년)", 3: " (3학년)"}
        for s in self.unavail:
            g = getattr(s, "grade", 0) or 0
            self.lw_ua.addItem(f"{s.teacher} {s.day} {s.period}교시{gn[g]}")

    # ───────── 유사과목 ─────────
    def add_grp(self):
        subs = [s for s, cb in self.subj_checks if cb.isChecked()]
        if len(subs) < 2:
            self._msg("안내", "과목을 2개 이상 체크하세요."); return
        name = self.ed_grp.text().strip() or f"그룹{len(self.similar)+1}"
        self.similar.append(SimilarSubjectGroup(name=name, subjects=subs))
        self.lw_grp.addItem(f"{name}: {'·'.join(subs)}")
        self.ed_grp.clear()
        for _, cb in self.subj_checks:
            cb.setChecked(False)

    def del_grp(self):
        rows = sorted({self.lw_grp.row(it) for it in self.lw_grp.selectedItems()}, reverse=True)
        for r in rows:
            del self.similar[r]; self.lw_grp.takeItem(r)

    # ───────── 실행 ─────────
    def run(self):
        # 버튼 clicked 시그널은 checked(bool)를 넘기므로, 인자 없는 슬롯으로 받는다.
        self._start_solve(self.warm_units)

    def _start_solve(self, warm_units):
        if self.data is None:
            self._msg("안내", "먼저 시수표를 열어주세요."); return
        if not HAS_CPSAT:
            self._msg("오류", "ortools가 설치되어 있지 않습니다.", QMessageBox.Warning); return
        if not isinstance(warm_units, list):
            warm_units = None
        params = self._collect_params(); time_limit = self.sp_time.value()
        self.txt_result.clear(); self.lb_penalty.setText("")
        self._toggle_running(True)
        self.worker = SolverWorker(self.data, list(self.non_class), list(self.unavail),
                                   list(self.similar), params, time_limit, warm_units)
        self.worker.progress.connect(self._on_progress)
        self.worker.done_ok.connect(self._on_done_ok)
        self.worker.done_none.connect(self._on_done_none)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def run_more(self):
        if self.result_state is not None:
            self._start_solve(list(self.result_state.pos))

    def stop(self):
        if self.worker is not None:
            self.worker.stop_event.set()
            self._set_status("중단 요청됨 — 최선해 정리 중...")

    def _toggle_running(self, running):
        self.btn_run.setEnabled(not running); self.btn_more.setEnabled(not running and self.result_state is not None)
        self.btn_stop.setEnabled(running)
        self.btn_save.setEnabled(not running and self.result_sol is not None and self._hard0)
        self.btn_sess_save.setEnabled(not running and self.result_state is not None)
        self.btn_sess_load.setEnabled(not running)
        self.pbar.setVisible(running)

    _hard0 = False

    def _on_progress(self, msg):
        self._set_status(msg)
        if "페널티" in msg:
            self.lb_penalty.setText("⏳ " + msg)

    def _on_done_ok(self, st, sol, status):
        self.result_state = st; self.result_sol = sol
        v = sol.violations
        hard = sum(val for k, val in v.items() if k in ("H2", "H3", "H4", "H6", "H8"))
        self._hard0 = (hard == 0)
        stopped = self.worker is not None and self.worker.stop_event.is_set()
        head = f"[{'중단됨' if stopped else status}] 총 페널티 {sol.penalty}  (하드 위반 {hard})"
        detail = (f"교사 3연속(H7) {v.get('H7',0)} · 같은과목같은날(H5) {v.get('H5',0)} · "
                  f"묶기 {v.get('Hpair',0)} · 2시간연속요일(H11) {v.get('H11',0)} · "
                  f"하루시수초과(S8) {v.get('S8',0)} · 유사과목(S9) {v.get('S9',0)}"
                  + (f" · 점심전후 {v.get('Lunch',0)}" if 'Lunch' in v else ""))
        tail = ("‘엑셀로 저장’ 또는 ‘더 돌리기’로 점수를 더 낮출 수 있습니다." if hard == 0
                else "⚠ 하드 위반이 남았습니다. ‘더 돌리기’ 또는 탐색 시간을 늘려보세요.")
        try:
            diag = st.diagnose_text()
        except Exception:
            diag = ""
        parts = [head, detail, "", tail, "", "──────── 페널티 상세 (누가·언제·왜) ────────", diag]
        self.txt_result.setPlainText("\n".join(parts))
        self.lb_penalty.setText(f"✅ 완료 — 총 페널티 {sol.penalty} (하드 {hard})")
        self._set_status("완료")
        self._toggle_running(False)
        try:
            self.load_editor()
        except Exception:
            pass

    def _on_done_none(self, status, report=""):
        if status == "INFEASIBLE":
            if report:
                msg = report + ("\n\n(참고: 묶음수업이 있는 학년은 여유 칸이 2칸 이상 "
                                "필요할 수 있습니다.)")
            else:
                msg = ("이 조건으로는 시간표를 만들 수 없습니다 (해가 존재하지 않음).\n\n"
                       "비수업 시간 또는 교사 불가시간이 시수표와 충돌합니다. 비수업을 1~2칸 "
                       "줄이거나 그 학년 시수를 줄여보세요.")
        else:
            msg = "시간 내 해를 찾지 못했습니다. 탐색 시간을 늘려보세요."
        self.txt_result.setPlainText(msg)
        self.lb_penalty.setText("⚠ 해 없음" if status == "INFEASIBLE" else "")
        self._set_status("해를 찾지 못함")
        self._toggle_running(False)

    def _on_failed(self, tb):
        self._msg("오류", tb, QMessageBox.Critical)
        self._set_status("오류")
        self._toggle_running(False)

    # ───────── 저장 ─────────
    def save_excel(self):
        if self.result_sol is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "엑셀로 저장",
                                              f"시간표_{self.ed_year.text()}_{self.cb_sem.currentText()}.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        try:
            params = self._collect_params()
            saved = output.save_excel(self.result_sol, self.data, list(self.non_class),
                                      list(self.unavail), int(self.ed_year.text()),
                                      int(self.cb_sem.currentText()), params, out_dir=os.path.dirname(path) or ".")
            if saved != path and os.path.exists(saved):
                os.replace(saved, path)
            self._msg("저장 완료", f"저장되었습니다:\n{path}")
        except Exception as e:
            self._msg("저장 실패", str(e), QMessageBox.Warning)

    def save_session(self):
        if self.result_state is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "이어돌리기 저장", "이어돌리기.json", "이어돌리기 (*.json)")
        if not path:
            return
        try:
            session_io.save_session(path, self.data, list(self.non_class), list(self.unavail),
                                    list(self.similar), self._collect_params(),
                                    int(self.ed_year.text()), int(self.cb_sem.currentText()),
                                    list(self.result_state.pos))
            self._msg("저장 완료", f"나중에 ‘이어돌리기 열기’로 더 돌릴 수 있습니다:\n{path}")
        except Exception as e:
            self._msg("저장 실패", str(e), QMessageBox.Warning)

    def load_session(self):
        path, _ = QFileDialog.getOpenFileName(self, "이어돌리기 열기", "", "이어돌리기 (*.json)")
        if not path:
            return
        try:
            s = session_io.load_session(path)
        except Exception as e:
            self._msg("열기 실패", str(e), QMessageBox.Warning); return
        self.data = s["data"]; self.non_class = s["non_class"]; self.unavail = s["unavail"]; self.similar = s["similar"]
        self.warm_units = s["warm_units"]
        self.ed_year.setText(str(s["year"])); self.cb_sem.setCurrentText(str(s["semester"]))
        p = s["params"]
        self.sp_mc.setValue(int(p.get("max_consecutive", 2))); self.sp_dn.setValue(int(p.get("daily_n", 1)))
        self.ck_lunch.setChecked(bool(p.get("lunch_split", False))); self.sp_lunchp.setValue(int(p.get("lunch_period", 4)))
        self.roles = [tuple(x) for x in p.get("roles_list", [])]
        self._after_data_loaded(os.path.basename(path) + " (이어돌리기)")
        self._render_roles()
        self._refresh_all_lists()
        self.txt_result.setPlainText("이전 시간표를 불러왔습니다. [시간표 생성]을 누르면 그 지점에서 이어서 최적화합니다.")


    def closeEvent(self, event):
        try:
            if self.worker is not None and self.worker.isRunning():
                self.worker.stop_event.set()
                self.worker.wait(3000)
        except Exception:
            pass
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
