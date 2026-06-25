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
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QLabel, QPushButton,
    QComboBox, QSpinBox, QCheckBox, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QProgressBar, QFileDialog, QMessageBox, QGroupBox,
    QGridLayout, QVBoxLayout, QHBoxLayout, QScrollArea, QFrame, QStatusBar,
)

from excel_parser import parse_excel
from scheduler import HybridScheduler
from models import NonClassSlot, TeacherUnavailable, SimilarSubjectGroup
import output
import session_io
import settings_io

try:
    from cpsat_solver import solve_cpsat, cpsat_available
    HAS_CPSAT = cpsat_available()
except Exception:
    HAS_CPSAT = False

DAYS = ["월", "화", "수", "목", "금"]
PERIODS = [1, 2, 3, 4, 5, 6, 7]

PENALTY_RULES = (
    "■ 반드시 지키는 규칙 (하드 — 위반 시 시간표로 못 씀)\n"
    "  · 학급 한 칸에 두 수업 금지 / 교사 한 칸에 두 수업 금지\n"
    "  · 비수업 시간·교사 불가시간에 배정 금지 / 묶음수업은 서로 다른 요일\n\n"
    "■ 최소화하는 규칙 (소프트 — 점수가 낮을수록 좋음)\n"
    "  · 같은 과목 같은 날 중복(H5, 100): 주 5시간 초과분은 불가피하므로 제외\n"
    "  · 교사 연속수업 초과(H7, 50): '최대 연속'을 넘는 연강마다\n"
    "  · 2시간 과목 연속 요일(H11, 30): 예) 월·화에 같은 2시간 과목\n"
    "  · 교사 하루 시수 초과(S8, 10): 평균+여유 초과분\n"
    "  · 유사과목 같은 날(S9, 5): 지정한 유사과목 그룹이 같은 날\n"
    "  · 묶기(25): 교사가 하루 3시수 이상인데 연강(2개 묶음)이 하나도 없을 때\n"
    "  · [선택] 점심 전후 연속(40): 한 교사가 점심 직전·직후 교시를 연달아 맡을 때"
)

STYLE = """
QWidget { background: #ffffff; color: #1f2937; font-family: 'Malgun Gothic','맑은 고딕'; font-size: 13px; }
QScrollArea, QScrollArea > QWidget > QWidget { background: #f8fafc; }
QGroupBox { border: 1px solid #e5e7eb; border-radius: 12px; margin-top: 16px; padding: 14px 12px 12px 12px; background: #ffffff; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 2px 8px; color: #2563eb; font-weight: 700; }
QPushButton { background: #2563eb; color: #ffffff; border: none; border-radius: 8px; padding: 7px 14px; font-weight: 600; }
QPushButton:hover { background: #1d4ed8; }
QPushButton:pressed { background: #1e40af; }
QPushButton:disabled { background: #cbd5e1; color: #eef2f7; }
QPushButton[kind="ghost"] { background: #f1f5f9; color: #334155; border: 1px solid #e2e8f0; }
QPushButton[kind="ghost"]:hover { background: #e2e8f0; }
QPushButton[kind="danger"] { background: #ef4444; }
QPushButton[kind="danger"]:hover { background: #dc2626; }
QComboBox, QSpinBox, QLineEdit { border: 1px solid #d1d5db; border-radius: 7px; padding: 5px 8px; background: #ffffff; }
QComboBox:focus, QSpinBox:focus, QLineEdit:focus { border: 1px solid #2563eb; }
QListWidget { border: 1px solid #e5e7eb; border-radius: 8px; background: #ffffff; padding: 2px; }
QPlainTextEdit { border: 1px solid #e5e7eb; border-radius: 8px; background: #ffffff; padding: 6px; }
QTabWidget::pane { border: 1px solid #e5e7eb; border-radius: 10px; top: -1px; background: #f8fafc; }
QTabBar::tab { background: #f1f5f9; color: #475569; padding: 8px 18px; border-top-left-radius: 9px; border-top-right-radius: 9px; margin-right: 3px; }
QTabBar::tab:selected { background: #ffffff; color: #2563eb; font-weight: 700; border: 1px solid #e5e7eb; border-bottom: none; }
QProgressBar { border: 1px solid #e5e7eb; border-radius: 7px; background: #f1f5f9; height: 16px; text-align: center; }
QProgressBar::chunk { background: #2563eb; border-radius: 6px; }
QCheckBox { spacing: 5px; }
QStatusBar { background: #f1f5f9; color: #64748b; }
QLabel[hint="true"] { color: #94a3b8; }
QLabel[role="penalty"] { color: #2563eb; font-weight: 700; }
"""


def resource_path(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


class SolverWorker(QThread):
    progress = Signal(str)
    done_ok = Signal(object, object, str)   # state, solution, status
    done_none = Signal(str)
    failed = Signal(str)

    def __init__(self, data, nc, ua, sim, params, time_limit, warm):
        super().__init__()
        self.data, self.nc, self.ua, self.sim = data, nc, ua, sim
        self.params, self.time_limit, self.warm = params, time_limit, warm
        self.stop_event = threading.Event()

    def run(self):
        try:
            sch = HybridScheduler(self.data, self.nc, self.ua, self.sim, self.params)
            st, status = solve_cpsat(sch, time_limit=self.time_limit,
                                     progress=lambda m: self.progress.emit(m),
                                     stop_event=self.stop_event, warm_units=self.warm)
            if st is None:
                self.done_none.emit(status)
                return
            sch.polish_pairing(st, rounds=6)
            self.done_ok.emit(st, st.get_solution(), status)
        except Exception:
            tb = traceback.format_exc()
            _log_error(tb)
            self.failed.emit(tb)


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
        self.subj_checks = []     # (과목명, QCheckBox)
        self.grid_checks = {}     # (day,period) -> QCheckBox
        self.row_all = {}         # day -> QCheckBox
        self._grid_updating = False
        self.result_sol = None
        self.result_state = None
        self.warm_units = None
        self.worker = None

        tabs = QTabWidget()
        tabs.addTab(self._build_input_tab(), "입력 / 생성")
        tabs.addTab(self._build_rules_tab(), "페널티 규칙")
        self.setCentralWidget(tabs)

        sb = QStatusBar()
        sb.addPermanentWidget(QLabel("made by 여양고 김동욱"))
        self.setStatusBar(sb)
        if not HAS_CPSAT:
            self._set_status("⚠ ortools 미설치 — build_exe.bat 으로 다시 빌드하세요")

        self.setStyleSheet(STYLE)
        self.resize(1000, 880)
        self.setMinimumWidth(960)

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
        top3.addWidget(_btn("이 교사 불가시간 등록", self.register_ua))
        top3.addWidget(_btn("격자 비우기", self.clear_grid, "ghost"))
        h3 = QLabel("(교사 선택 → 칸 체크 → 등록)"); h3.setProperty("hint", "true")
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

        # 5. 설정
        g5 = QGroupBox("5. 설정")
        l5 = QGridLayout(g5)
        l5.addWidget(QLabel("교사 최대 연속"), 0, 0)
        self.sp_mc = QSpinBox(); self.sp_mc.setRange(1, 7); self.sp_mc.setValue(2); l5.addWidget(self.sp_mc, 0, 1)
        l5.addWidget(QLabel("하루 시수 여유(+n)"), 0, 2)
        self.sp_dn = QSpinBox(); self.sp_dn.setRange(0, 5); self.sp_dn.setValue(1); l5.addWidget(self.sp_dn, 0, 3)
        l5.addWidget(QLabel("탐색 시간(초)"), 0, 4)
        self.sp_time = QSpinBox(); self.sp_time.setRange(10, 1800); self.sp_time.setSingleStep(10); self.sp_time.setValue(90); l5.addWidget(self.sp_time, 0, 5)
        self.ck_lunch = QCheckBox("점심 전후 연속수업 방지"); l5.addWidget(self.ck_lunch, 1, 0, 1, 2)
        l5.addWidget(QLabel("점심 직전 교시"), 1, 2)
        self.sp_lunchp = QSpinBox(); self.sp_lunchp.setRange(1, 6); self.sp_lunchp.setValue(4); l5.addWidget(self.sp_lunchp, 1, 3)
        v.addWidget(g5)

        # 6. 실행 / 결과
        g6 = QGroupBox("6. 생성")
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
        self.txt_result = QPlainTextEdit(); self.txt_result.setReadOnly(True); self.txt_result.setFixedHeight(120)
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
        return dict(max_consecutive=self.sp_mc.value(), daily_n=self.sp_dn.value(),
                    lunch_split=self.ck_lunch.isChecked(), lunch_period=self.sp_lunchp.value())

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
                                           int(self.cb_sem.currentText()), params)
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

    def _on_ua_teacher(self):
        t = self.cb_ua_teacher.currentText()
        cur = {(s.day, s.period) for s in self.unavail if s.teacher == t}
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
        cnt = sum(1 for s in self.unavail if s.teacher == t)
        self._msg("등록 완료", f"{t} 선생님의 불가시간 {cnt}칸을 등록했습니다.")

    def _sync_current_grid(self):
        # 현재 선택된 교사의 격자 체크 상태를 self.unavail 에 반영(등록 누락 방지).
        t = self.cb_ua_teacher.currentText()
        if not t:
            return
        self.unavail = [s for s in self.unavail if s.teacher != t]
        for (day, p), cb in self.grid_checks.items():
            if cb.isChecked():
                self.unavail.append(TeacherUnavailable(teacher=t, day=day, period=p))
        self._render_ua()

    def del_ua(self):
        keep_rows = {self.lw_ua.row(it) for it in self.lw_ua.selectedItems()}
        new = [s for i, s in enumerate(self.unavail) if i not in keep_rows]
        self.unavail = new
        self._render_ua()

    def _render_ua(self):
        self.lw_ua.clear()
        for s in self.unavail:
            self.lw_ua.addItem(f"{s.teacher} {s.day} {s.period}교시")

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
        self.txt_result.setPlainText("\n".join([head, detail, "", tail]))
        self.lb_penalty.setText(f"✅ 완료 — 총 페널티 {sol.penalty} (하드 {hard})")
        self._set_status("완료")
        self._toggle_running(False)

    def _on_done_none(self, status):
        if status == "INFEASIBLE":
            msg = ("이 조건으로는 시간표를 만들 수 없습니다 (해가 존재하지 않음).\n\n"
                   "비수업 시간 또는 교사 불가시간이 시수표와 충돌합니다.\n"
                   "어떤 학년이 ‘주당 시수 = 남는 칸’으로 꽉 차 있으면, 비수업을 1~2칸 줄이거나 "
                   "그 학년 시수를 줄여야 합니다. (묶음수업이 있는 학년은 여유 칸이 2칸 이상 필요할 수 있습니다.)")
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
        self._after_data_loaded(os.path.basename(path) + " (이어돌리기)")
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
