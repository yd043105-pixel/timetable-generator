# -*- coding: utf-8 -*-
"""학교 시간표 생성기 — 데스크톱 GUI (exe용).

입력 양식 + 페널티 규칙 설명 + 점심 옵션 + 양식 다운로드 + 비수업 학년별 표시
+ 유사과목 체크박스 + 실시간 진행상황 + 중단 + 이어서 돌리기(세션 저장/불러오기).
"""
import os
import sys
import threading
import queue
import traceback
import shutil

# --windowed(콘솔 없는) 빌드에서는 sys.stdout/stderr 가 None 이라, ortools 등이
# 내부 로그를 출력하려다 프로세스가 통째로 종료될 수 있다. 더미 스트림으로 막는다.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# 처리되지 않은 모든 예외를 바탕화면/홈의 로그 파일에 남겨 원인 추적을 돕는다.
_LOG_PATH = os.path.join(os.path.expanduser("~"), "시간표생성기_오류기록.txt")


def _log_error(text):
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            import datetime
            f.write("\n===== " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " =====\n")
            f.write(text + "\n")
    except Exception:
        pass


def _excepthook(exc_type, exc, tb):
    _log_error("".join(traceback.format_exception(exc_type, exc, tb)))


sys.excepthook = _excepthook

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

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


def resource_path(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


class App:
    def __init__(self, root):
        self.root = root
        root.title("학교 시간표 생성기 (CP-SAT)")
        self.data = None
        self.non_class = []
        self.unavail = []
        self.similar = []
        self.subj_vars = []        # 유사과목 체크박스 (과목명, IntVar)
        self.result_sol = None
        self.result_state = None   # 이어서 돌리기용 배치 보관
        self.warm_units = None     # 불러온 세션의 배치
        self.stop_event = None
        self.running = False
        self.q = queue.Queue()

        # 하단 상태표시줄 (제작자 표기) — 노트북보다 먼저 배치해 하단 고정
        status_bar = ttk.Frame(root, relief="sunken", padding=(8, 2))
        status_bar.pack(side="bottom", fill="x")
        ttk.Label(status_bar, text="made by 여양고 김동욱",
                  anchor="e", foreground="#666").pack(side="right")

        nb = ttk.Notebook(root)
        nb.pack(side="top", fill="both", expand=True, padx=8, pady=8)
        self.tab_in = ttk.Frame(nb)
        self.tab_rule = ttk.Frame(nb)
        nb.add(self.tab_in, text="입력 / 생성")
        nb.add(self.tab_rule, text="페널티 규칙")
        self._build_input(self.tab_in)
        self._build_rules(self.tab_rule)
        self._finalize_window()
        self.root.after(120, self._poll)

    def _finalize_window(self):
        """모든 구성요소가 보이도록 창 크기를 정하고 고정한다."""
        self.root.update_idletasks()
        content_h = 1000
        for child in self.tab_in.winfo_children():
            if isinstance(child, tk.Canvas):
                for inner in child.winfo_children():
                    content_h = inner.winfo_reqheight()
        want_w = 1060
        want_h = content_h + 110  # 탭 헤더 + 상태표시줄 + 여유
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w = min(want_w, sw - 40)
        h = min(want_h, sh - 60)
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2 - 20)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.resizable(False, False)

    def _build_rules(self, parent):
        t = tk.Text(parent, wrap="word", font=("Malgun Gothic", 11), padx=14, pady=14)
        t.pack(fill="both", expand=True)
        t.insert("1.0", PENALTY_RULES)
        t.config(state="disabled")

    def _build_input(self, parent):
        canvas = tk.Canvas(parent, highlightthickness=0)
        sb = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        frame = ttk.Frame(canvas)
        frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=frame, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        # 마우스 휠 스크롤
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
        pad = dict(padx=6, pady=3)

        # 1. 기본 + 파일 + 양식 다운로드
        f1 = ttk.LabelFrame(frame, text="1. 기본 정보 · 시수표")
        f1.pack(fill="x", padx=8, pady=6)
        ttk.Label(f1, text="학년도").grid(row=0, column=0, **pad)
        self.var_year = tk.StringVar(value="2026")
        ttk.Entry(f1, textvariable=self.var_year, width=8).grid(row=0, column=1, **pad)
        ttk.Label(f1, text="학기").grid(row=0, column=2, **pad)
        self.var_sem = tk.StringVar(value="1")
        ttk.Combobox(f1, textvariable=self.var_sem, values=["1", "2"], width=4,
                     state="readonly").grid(row=0, column=3, **pad)
        ttk.Button(f1, text="시수표 양식 다운로드", command=self.download_template).grid(row=0, column=4, **pad)
        ttk.Button(f1, text="시수표(.xlsx) 열기", command=self.load_file).grid(row=0, column=5, **pad)
        self.lbl_file = ttk.Label(f1, text="파일 없음", foreground="#888")
        self.lbl_file.grid(row=1, column=0, columnspan=6, sticky="w", **pad)
        ttk.Button(f1, text="설정 저장(엑셀)", command=self.save_settings).grid(row=2, column=4, **pad)
        ttk.Button(f1, text="설정 불러오기(엑셀)", command=self.load_settings).grid(row=2, column=5, **pad)
        ttk.Label(f1, text="비수업·교사불가·유사그룹 등 입력 조건을 엑셀로 보관/재적용",
                  foreground="#888").grid(row=2, column=0, columnspan=4, sticky="w", **pad)

        # 2. 비수업 시간 — 학년별 가로 배치
        f2 = ttk.LabelFrame(frame, text="2. 비수업 시간 (그 학년 전체가 수업 불가)")
        f2.pack(fill="x", padx=8, pady=6)
        top = ttk.Frame(f2); top.pack(fill="x")
        self.nc_day = ttk.Combobox(top, values=DAYS, width=4, state="readonly"); self.nc_day.current(0)
        self.nc_day.pack(side="left", **pad)
        self.nc_period = ttk.Combobox(top, values=[str(p) for p in PERIODS], width=4, state="readonly"); self.nc_period.current(0)
        self.nc_period.pack(side="left", **pad)
        ttk.Button(top, text="1학년 추가", command=lambda: self.add_nc([1])).pack(side="left", **pad)
        ttk.Button(top, text="2학년 추가", command=lambda: self.add_nc([2])).pack(side="left", **pad)
        ttk.Button(top, text="3학년 추가", command=lambda: self.add_nc([3])).pack(side="left", **pad)
        ttk.Button(top, text="전체학년 추가", command=lambda: self.add_nc([1, 2, 3])).pack(side="left", **pad)
        cols = ttk.Frame(f2); cols.pack(fill="x")
        self.nc_boxes = {}
        for i, g in enumerate([1, 2, 3]):
            col = ttk.Frame(cols); col.grid(row=0, column=i, padx=8, pady=4, sticky="n")
            ttk.Label(col, text=f"{g}학년", font=("Malgun Gothic", 10, "bold")).pack()
            lb = tk.Listbox(col, height=5, width=16, selectmode="extended")
            lb.pack()
            ttk.Button(col, text="선택 삭제", command=lambda gg=g: self.del_nc(gg)).pack(pady=2)
            self.nc_boxes[g] = lb

        # 3. 교사 불가시간 — 교사 선택 + 요일×교시 격자 체크박스
        f3 = ttk.LabelFrame(frame, text="3. 교사 불가시간")
        f3.pack(fill="x", padx=8, pady=6)
        top3 = ttk.Frame(f3); top3.pack(fill="x")
        ttk.Label(top3, text="교사").pack(side="left", **pad)
        self.ua_teacher = ttk.Combobox(top3, values=[], width=14, state="readonly")
        self.ua_teacher.pack(side="left", **pad)
        self.ua_teacher.bind("<<ComboboxSelected>>", lambda e: self._on_ua_teacher())
        ttk.Button(top3, text="이 교사 불가시간 등록", command=self.register_ua).pack(side="left", **pad)
        ttk.Button(top3, text="격자 비우기", command=self.clear_grid).pack(side="left", **pad)
        ttk.Label(top3, text="(교사 선택 → 칸 체크 → 등록)", foreground="#888").pack(side="left", **pad)

        grid = ttk.Frame(f3); grid.pack(fill="x", pady=4)
        ttk.Label(grid, text="", width=4).grid(row=0, column=0)
        for j, p in enumerate(PERIODS):
            ttk.Label(grid, text=f"{p}교시", width=5, anchor="center").grid(row=0, column=j + 1, padx=1)
        ttk.Label(grid, text="전체", width=5, anchor="center",
                  foreground="#06c").grid(row=0, column=len(PERIODS) + 1, padx=(8, 1))
        self.grid_vars = {}
        self.row_all_vars = {}
        self._grid_updating = False
        for i, day in enumerate(DAYS):
            ttk.Label(grid, text=day, width=4, anchor="center").grid(row=i + 1, column=0)
            for j, p in enumerate(PERIODS):
                v = tk.IntVar()
                v.trace_add("write", lambda *a, d=day: self._on_cell_changed(d))
                ttk.Checkbutton(grid, variable=v).grid(row=i + 1, column=j + 1, padx=1, pady=1)
                self.grid_vars[(day, p)] = v
            av = tk.IntVar()
            ttk.Checkbutton(grid, variable=av,
                            command=lambda d=day: self._toggle_row_all(d)).grid(
                row=i + 1, column=len(PERIODS) + 1, padx=(8, 1))
            self.row_all_vars[day] = av

        ttk.Label(f3, text="등록된 불가시간 (체크 후 ‘선택 삭제’):", foreground="#555").pack(anchor="w", padx=6)
        bot3 = ttk.Frame(f3); bot3.pack(fill="x")
        c3 = tk.Canvas(bot3, height=72, highlightthickness=0)
        sb3 = ttk.Scrollbar(bot3, orient="vertical", command=c3.yview)
        self.ua_frame = ttk.Frame(c3)
        self.ua_frame.bind("<Configure>", lambda e: c3.configure(scrollregion=c3.bbox("all")))
        c3.create_window((0, 0), window=self.ua_frame, anchor="nw")
        c3.configure(yscrollcommand=sb3.set)
        c3.pack(side="left", fill="x", expand=True); sb3.pack(side="right", fill="y")
        ttk.Button(f3, text="선택 삭제", command=self.del_ua).pack(anchor="e", padx=6, pady=2)
        self.ua_vars = []  # (IntVar, TeacherUnavailable)

        # 4. 유사과목 그룹 — 체크박스
        f4 = ttk.LabelFrame(frame, text="4. 유사과목 그룹 (같은 학급 같은 날 회피)")
        f4.pack(fill="x", padx=8, pady=6)
        ttk.Label(f4, text="과목 체크 →").grid(row=0, column=0, sticky="nw", **pad)
        sc = ttk.Frame(f4); sc.grid(row=0, column=1, **pad)
        scc = tk.Canvas(sc, width=440, height=120, highlightthickness=0)
        ssb = ttk.Scrollbar(sc, orient="vertical", command=scc.yview)
        self.subj_frame = ttk.Frame(scc)
        self.subj_frame.bind("<Configure>", lambda e: scc.configure(scrollregion=scc.bbox("all")))
        scc.create_window((0, 0), window=self.subj_frame, anchor="nw")
        scc.configure(yscrollcommand=ssb.set)
        scc.pack(side="left"); ssb.pack(side="right", fill="y")
        right = ttk.Frame(f4); right.grid(row=0, column=2, sticky="n", **pad)
        ttk.Label(right, text="그룹 이름").pack()
        self.var_grp = tk.StringVar()
        ttk.Entry(right, textvariable=self.var_grp, width=16).pack(pady=2)
        ttk.Button(right, text="그룹 추가", command=self.add_grp).pack(pady=2)
        ttk.Button(right, text="선택 삭제", command=self.del_grp).pack(pady=2)
        self.lb_grp = tk.Listbox(f4, height=4, width=70)
        self.lb_grp.grid(row=1, column=0, columnspan=3, sticky="we", **pad)

        # 5. 설정
        f5 = ttk.LabelFrame(frame, text="5. 설정")
        f5.pack(fill="x", padx=8, pady=6)
        ttk.Label(f5, text="교사 최대 연속").grid(row=0, column=0, **pad)
        self.var_mc = tk.StringVar(value="2")
        ttk.Spinbox(f5, from_=1, to=7, textvariable=self.var_mc, width=4).grid(row=0, column=1, **pad)
        ttk.Label(f5, text="하루 시수 여유(+n)").grid(row=0, column=2, **pad)
        self.var_dn = tk.StringVar(value="1")
        ttk.Spinbox(f5, from_=0, to=5, textvariable=self.var_dn, width=4).grid(row=0, column=3, **pad)
        ttk.Label(f5, text="탐색 시간(초)").grid(row=0, column=4, **pad)
        self.var_time = tk.StringVar(value="90")
        ttk.Spinbox(f5, from_=10, to=1800, increment=10, textvariable=self.var_time, width=6).grid(row=0, column=5, **pad)
        self.var_lunch = tk.BooleanVar(value=False)
        ttk.Checkbutton(f5, text="점심 전후 연속수업 방지", variable=self.var_lunch).grid(row=1, column=0, columnspan=2, sticky="w", **pad)
        ttk.Label(f5, text="점심 직전 교시").grid(row=1, column=2, **pad)
        self.var_lunchp = tk.StringVar(value="4")
        ttk.Spinbox(f5, from_=1, to=6, textvariable=self.var_lunchp, width=4).grid(row=1, column=3, **pad)

        # 6. 실행 / 진행 / 결과
        f6 = ttk.Frame(frame); f6.pack(fill="x", padx=8, pady=8)
        self.btn_run = ttk.Button(f6, text="시간표 생성", command=self.run)
        self.btn_run.pack(side="left")
        self.btn_more = ttk.Button(f6, text="더 돌리기(이어서)", command=self.run_more, state="disabled")
        self.btn_more.pack(side="left", padx=4)
        self.btn_stop = ttk.Button(f6, text="중단", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left", padx=4)
        self.btn_save = ttk.Button(f6, text="엑셀로 저장", command=self.save, state="disabled")
        self.btn_save.pack(side="left", padx=12)
        self.btn_sess_save = ttk.Button(f6, text="이어돌리기 저장", command=self.save_session, state="disabled")
        self.btn_sess_save.pack(side="left", padx=4)
        self.btn_sess_load = ttk.Button(f6, text="이어돌리기 열기", command=self.load_session)
        self.btn_sess_load.pack(side="left", padx=4)

        self.var_prog = tk.DoubleVar(value=0)
        self.pbar = ttk.Progressbar(frame, mode="indeterminate")
        self.lbl_status = ttk.Label(frame, text="" if HAS_CPSAT else "⚠ ortools 미설치", foreground="#06c")
        self.lbl_status.pack(anchor="w", padx=10)
        self.txt_result = tk.Text(frame, height=7, wrap="word", font=("Malgun Gothic", 10))
        self.txt_result.pack(fill="both", expand=True, padx=8, pady=6)

    # ───────── 데이터 입력 동작 ─────────
    def download_template(self):
        src = resource_path("template.xlsx")
        if not os.path.exists(src):
            messagebox.showerror("오류", "양식 파일을 찾을 수 없습니다.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".xlsx",
                                            initialfile="교사별_시수표_양식.xlsx",
                                            filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        try:
            shutil.copyfile(src, path)
            messagebox.showinfo("저장 완료", f"양식을 저장했습니다:\n{path}")
        except Exception as e:
            messagebox.showerror("저장 실패", str(e))

    def load_file(self):
        path = filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx *.xls")])
        if not path:
            return
        try:
            self.data = parse_excel(path)
        except Exception as e:
            messagebox.showerror("읽기 실패", str(e))
            return
        self.warm_units = None
        self._after_data_loaded(os.path.basename(path))

    def _after_data_loaded(self, label):
        d = self.data
        ncls = sum(len(v) for v in d.classes_per_grade.values())
        self.lbl_file.config(text=f"✓ {label} — 학급 {ncls}, 교사 {len(d.teachers)}, 묶음 {len(d.bundle_groups)}",
                             foreground="#070")
        self.ua_teacher.config(values=d.teachers)
        if d.teachers:
            self.ua_teacher.current(0)
        # 유사과목 체크박스 다시 그림
        for w in self.subj_frame.winfo_children():
            w.destroy()
        self.subj_vars = []
        for i, s in enumerate(d.subjects):
            v = tk.IntVar()
            ttk.Checkbutton(self.subj_frame, text=s, variable=v).grid(
                row=i // 3, column=i % 3, sticky="w", padx=6, pady=1)
            self.subj_vars.append((s, v))

    def add_nc(self, grades):
        day = self.nc_day.get(); period = int(self.nc_period.get())
        for g in grades:
            if not any(s.grade == g and s.day == day and s.period == period for s in self.non_class):
                self.non_class.append(NonClassSlot(grade=g, day=day, period=period))
                self.nc_boxes[g].insert("end", f"{day} {period}교시")

    def del_nc(self, g):
        lb = self.nc_boxes[g]
        sel = list(lb.curselection())
        items = [lb.get(i) for i in sel]
        for txt in items:
            day, per = txt.split(" ")
            per = int(per.replace("교시", ""))
            self.non_class = [s for s in self.non_class
                              if not (s.grade == g and s.day == day and s.period == per)]
        for i in reversed(sel):
            lb.delete(i)

    def _on_cell_changed(self, day):
        # 개별 교시 변경 → 그 요일 '전체' 자동 갱신 (전부 체크면 켜짐)
        if self._grid_updating:
            return
        all_on = all(self.grid_vars[(day, p)].get() == 1 for p in PERIODS)
        self._grid_updating = True
        self.row_all_vars[day].set(1 if all_on else 0)
        self._grid_updating = False

    def _toggle_row_all(self, day):
        # '전체' 클릭 → 그 요일 1~7교시 모두 같은 값으로
        val = self.row_all_vars[day].get()
        self._grid_updating = True
        for p in PERIODS:
            self.grid_vars[(day, p)].set(val)
        self._grid_updating = False

    def _on_ua_teacher(self):
        t = self.ua_teacher.get()
        cur = {(s.day, s.period) for s in self.unavail if s.teacher == t}
        self._grid_updating = True
        for (day, p), v in self.grid_vars.items():
            v.set(1 if (day, p) in cur else 0)
        self._grid_updating = False
        for day in DAYS:
            self._on_cell_changed(day)

    def clear_grid(self):
        self._grid_updating = True
        for v in self.grid_vars.values():
            v.set(0)
        for av in self.row_all_vars.values():
            av.set(0)
        self._grid_updating = False

    def register_ua(self):
        t = self.ua_teacher.get()
        if not t:
            messagebox.showinfo("안내", "교사를 먼저 선택하세요.")
            return
        # 이 교사의 기존 등록을 격자 상태로 동기화(추가+삭제 동시)
        self.unavail = [s for s in self.unavail if s.teacher != t]
        cnt = 0
        for (day, p), v in self.grid_vars.items():
            if v.get() == 1:
                self.unavail.append(TeacherUnavailable(teacher=t, day=day, period=p))
                cnt += 1
        self._render_ua()
        messagebox.showinfo("등록 완료", f"{t} 선생님의 불가시간 {cnt}칸을 등록했습니다.")

    def del_ua(self):
        self.unavail = [s for (v, s) in self.ua_vars if v.get() == 0]
        self._render_ua()

    def _render_ua(self):
        for w in self.ua_frame.winfo_children():
            w.destroy()
        self.ua_vars = []
        COLS = 5
        for i, s in enumerate(self.unavail):
            v = tk.IntVar()
            ttk.Checkbutton(self.ua_frame, text=f"{s.teacher} {s.day} {s.period}교시",
                            variable=v).grid(row=i // COLS, column=i % COLS, sticky="w", padx=4, pady=1)
            self.ua_vars.append((v, s))

    def add_grp(self):
        subs = [s for s, v in self.subj_vars if v.get() == 1]
        if len(subs) < 2:
            messagebox.showinfo("안내", "과목을 2개 이상 체크하세요.")
            return
        name = self.var_grp.get().strip() or f"그룹{len(self.similar)+1}"
        self.similar.append(SimilarSubjectGroup(name=name, subjects=subs))
        self.lb_grp.insert("end", f"{name}: {'·'.join(subs)}")
        self.var_grp.set("")
        for _, v in self.subj_vars:
            v.set(0)

    def del_grp(self):
        for i in reversed(self.lb_grp.curselection()):
            self.lb_grp.delete(i); del self.similar[i]

    def _rebuild_lists_from_session(self):
        # 세션 불러온 뒤 화면 목록 다시 채우기
        for g in (1, 2, 3):
            self.nc_boxes[g].delete(0, "end")
        for s in self.non_class:
            self.nc_boxes[s.grade].insert("end", f"{s.day} {s.period}교시")
        self._render_ua()
        self.lb_grp.delete(0, "end")
        for s in self.similar:
            self.lb_grp.insert("end", f"{s.name}: {'·'.join(s.subjects)}")

    # ───────── 실행 ─────────
    def _collect_params(self):
        return dict(max_consecutive=int(self.var_mc.get()), daily_n=int(self.var_dn.get()),
                    lunch_split=bool(self.var_lunch.get()), lunch_period=int(self.var_lunchp.get()))

    def save_settings(self):
        path = filedialog.asksaveasfilename(defaultextension=".xlsx",
                                            initialfile="시간표설정.xlsx",
                                            filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        try:
            params = self._collect_params()
            params["time_limit"] = int(self.var_time.get())
            settings_io.save_settings_xlsx(path, list(self.non_class), list(self.unavail),
                                           list(self.similar), int(self.var_year.get()),
                                           int(self.var_sem.get()), params)
            messagebox.showinfo("저장 완료",
                                f"입력 설정을 저장했습니다:\n{path}\n\n"
                                "다음에 ‘설정 불러오기(엑셀)’로 그대로 적용할 수 있습니다.")
        except Exception as e:
            messagebox.showerror("저장 실패", str(e))

    def load_settings(self):
        path = filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx *.xls")])
        if not path:
            return
        try:
            r = settings_io.load_settings_xlsx(path)
        except Exception as e:
            messagebox.showerror("불러오기 실패", str(e))
            return
        self.non_class = r["non_class"]
        self.unavail = r["unavail"]
        self.similar = r["similar"]
        s = r.get("settings") or {}
        if s:
            self.var_year.set(str(s.get("year", self.var_year.get())))
            self.var_sem.set(str(s.get("semester", self.var_sem.get())))
            self.var_mc.set(str(s.get("max_consecutive", 2)))
            self.var_dn.set(str(s.get("daily_n", 1)))
            self.var_time.set(str(s.get("time_limit", 90)))
            self.var_lunch.set(bool(s.get("lunch_split", False)))
            self.var_lunchp.set(str(s.get("lunch_period", 4)))
        self._rebuild_lists_from_session()
        miss = ""
        if self.data is not None:
            unknown = sorted({u.teacher for u in self.unavail} - set(self.data.teachers))
            if unknown:
                miss = "\n\n※ 현재 시수표에 없는 교사: " + ", ".join(unknown)
        messagebox.showinfo("불러옴",
                            f"설정을 적용했습니다.\n"
                            f"비수업 {len(self.non_class)} · 교사불가 {len(self.unavail)} · "
                            f"유사그룹 {len(self.similar)}" + miss)

    def run(self, warm=None):
        if self.data is None:
            messagebox.showinfo("안내", "먼저 시수표를 열어주세요.")
            return
        if not HAS_CPSAT:
            messagebox.showerror("오류", "ortools가 설치되어 있지 않습니다.")
            return
        try:
            params = self._collect_params(); time_limit = int(self.var_time.get())
        except ValueError:
            messagebox.showerror("오류", "설정값을 확인하세요.")
            return
        self.running = True
        self.stop_event = threading.Event()
        self.btn_run.config(state="disabled"); self.btn_more.config(state="disabled")
        self.btn_save.config(state="disabled"); self.btn_sess_save.config(state="disabled")
        self.btn_sess_load.config(state="disabled"); self.btn_stop.config(state="normal")
        self.txt_result.delete("1.0", "end")
        self.pbar.pack(fill="x", padx=10, pady=2); self.pbar.start(12)
        threading.Thread(target=self._worker, args=(params, time_limit, warm), daemon=True).start()

    def run_more(self):
        if self.result_state is not None:
            self.run(warm=list(self.result_state.pos))

    def stop(self):
        if self.stop_event is not None:
            self.stop_event.set()
            self.lbl_status.config(text="중단 요청됨 — 최선해 정리 중...")

    def _worker(self, params, time_limit, warm):
        try:
            warm_units = warm if warm is not None else self.warm_units
            sch = HybridScheduler(self.data, list(self.non_class), list(self.unavail),
                                  list(self.similar), params)
            st, status = solve_cpsat(sch, time_limit=time_limit,
                                     progress=lambda m: self.q.put(("status", m)),
                                     stop_event=self.stop_event, warm_units=warm_units)
            if st is None:
                if status == "INFEASIBLE":
                    msg = ("이 조건으로는 시간표를 만들 수 없습니다 (해가 존재하지 않음).\n\n"
                           "비수업 시간 또는 교사 불가시간이 시수표와 충돌합니다.\n"
                           "특히 어떤 학년이 ‘주당 시수 = 남는 칸’으로 꽉 차 있으면, "
                           "비수업을 1~2칸 줄이거나 그 학년 시수를 줄여야 합니다.\n"
                           "(묶음수업이 있는 학년은 여유 칸이 2칸 이상 필요할 수 있습니다.)")
                else:
                    msg = "시간 내 해를 찾지 못했습니다. 탐색 시간을 늘려보세요."
                self.q.put(("done", msg))
                return
            sch.polish_pairing(st, rounds=6)
            self.warm_units = None  # 1회용 (불러온 세션) 소진
            self.q.put(("result", st, st.get_solution(), status))
        except Exception:
            tb = traceback.format_exc()
            _log_error(tb)
            self.q.put(("error", tb))

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == "status":
                    self.lbl_status.config(text=msg[1])
                elif msg[0] == "result":
                    self._show_result(msg[1], msg[2], msg[3])
                elif msg[0] == "done":
                    self._finish_run()
                    self.lbl_status.config(text="완료")
                    self.txt_result.insert("end", msg[1])
                elif msg[0] == "error":
                    self._finish_run()
                    self.lbl_status.config(text="오류")
                    messagebox.showerror("오류", msg[1])
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

    def _finish_run(self):
        self.running = False
        self.pbar.stop(); self.pbar.pack_forget()
        self.btn_run.config(state="normal"); self.btn_stop.config(state="disabled")
        self.btn_sess_load.config(state="normal")

    def _show_result(self, st, sol, status):
        self._finish_run()
        self.result_sol = sol; self.result_state = st
        v = sol.violations
        hard = sum(val for k, val in v.items() if k in ("H2", "H3", "H4", "H6", "H8"))
        stopped = self.stop_event is not None and self.stop_event.is_set()
        head = f"[{'중단됨' if stopped else status}] 총 페널티 {sol.penalty}  (하드 위반 {hard})"
        detail = (f"  교사 3연속(H7) {v.get('H7',0)} · 같은과목같은날(H5) {v.get('H5',0)} · "
                  f"묶기 {v.get('Hpair',0)} · 2시간연속요일(H11) {v.get('H11',0)} · "
                  f"하루시수초과(S8) {v.get('S8',0)} · 유사과목(S9) {v.get('S9',0)}"
                  + (f" · 점심전후 {v.get('Lunch',0)}" if 'Lunch' in v else ""))
        tail = ("‘엑셀로 저장’으로 받거나 ‘더 돌리기’로 점수를 더 낮출 수 있습니다." if hard == 0
                else "⚠ 하드 위반이 남았습니다. ‘더 돌리기’ 또는 시간을 늘려보세요.")
        self.lbl_status.config(text="완료")
        self.txt_result.insert("end", "\n".join([head, detail, "", tail]))
        self.btn_more.config(state="normal")
        self.btn_sess_save.config(state="normal")
        if hard == 0:
            self.btn_save.config(state="normal")

    # ───────── 저장 ─────────
    def save(self):
        if self.result_sol is None:
            return
        path = filedialog.asksaveasfilename(defaultextension=".xlsx",
                                            initialfile=f"시간표_{self.var_year.get()}_{self.var_sem.get()}.xlsx",
                                            filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        try:
            params = self._collect_params()
            saved = output.save_excel(self.result_sol, self.data, list(self.non_class),
                                      list(self.unavail), int(self.var_year.get()),
                                      int(self.var_sem.get()), params,
                                      out_dir=os.path.dirname(path) or ".")
            try:
                if saved != path and os.path.exists(saved):
                    os.replace(saved, path)
            except Exception:
                path = saved
            messagebox.showinfo("저장 완료", f"저장되었습니다:\n{path}")
        except Exception as e:
            messagebox.showerror("저장 실패", str(e))

    def save_session(self):
        if self.result_state is None:
            return
        path = filedialog.asksaveasfilename(defaultextension=".json",
                                            initialfile="이어돌리기.json",
                                            filetypes=[("이어돌리기 파일", "*.json")])
        if not path:
            return
        try:
            session_io.save_session(path, self.data, list(self.non_class), list(self.unavail),
                                    list(self.similar), self._collect_params(),
                                    int(self.var_year.get()), int(self.var_sem.get()),
                                    list(self.result_state.pos))
            messagebox.showinfo("저장 완료", f"나중에 ‘이어돌리기 열기’로 불러와 더 돌릴 수 있습니다:\n{path}")
        except Exception as e:
            messagebox.showerror("저장 실패", str(e))

    def load_session(self):
        path = filedialog.askopenfilename(filetypes=[("이어돌리기 파일", "*.json")])
        if not path:
            return
        try:
            s = session_io.load_session(path)
        except Exception as e:
            messagebox.showerror("열기 실패", str(e))
            return
        self.data = s["data"]
        self.non_class = s["non_class"]; self.unavail = s["unavail"]; self.similar = s["similar"]
        self.warm_units = s["warm_units"]
        self.var_year.set(str(s["year"])); self.var_sem.set(str(s["semester"]))
        p = s["params"]
        self.var_mc.set(str(p.get("max_consecutive", 2)))
        self.var_dn.set(str(p.get("daily_n", 1)))
        self.var_lunch.set(bool(p.get("lunch_split", False)))
        self.var_lunchp.set(str(p.get("lunch_period", 4)))
        self._after_data_loaded(os.path.basename(path) + " (이어돌리기)")
        self._rebuild_lists_from_session()
        self.txt_result.delete("1.0", "end")
        self.txt_result.insert("end", "이전 시간표를 불러왔습니다. [시간표 생성]을 누르면 그 지점에서 이어서 최적화합니다.")
        messagebox.showinfo("불러옴", "‘시간표 생성’을 누르면 이전 결과에서 이어서 돌립니다.")


def main():
    root = tk.Tk()
    try:
        if sys.platform.startswith("win"):
            root.call("tk", "scaling", 1.2)
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
