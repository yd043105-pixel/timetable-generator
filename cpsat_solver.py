"""CP-SAT 기반 시간표 솔버.

H2(학급충돌)/H3(교사충돌)/H8(묶음 요일분산)만 하드로 두고, 나머지는 실제
페널티 가중치 그대로 소프트로 최소화한다. 하드충족해를 힌트로 줘서 항상 빠르게
첫 해를 확보하고 시간만큼 개선한다. ortools가 없으면 import 시 ImportError.
"""
from collections import defaultdict
from ortools.sat.python import cp_model
from scheduler import (State, PEN_SAME_DAY, PEN_CONSEC, PEN_2H_CONSEC_DAY,
                       PEN_TEACHER_DAILY, PEN_SIMILAR_SAME_DAY, PEN_FRAGMENT,
                       PEN_LUNCH_CROSS, DAYS)

DAY_IDX = {d: i for i, d in enumerate(DAYS)}


def _sid(d, p):
    return DAY_IDX[d] * 7 + (p - 1)


def cpsat_available():
    try:
        from ortools.sat.python import cp_model  # noqa
        return True
    except Exception:
        return False


def solve_cpsat(sch, time_limit=90, workers=8, progress=None,
                stop_event=None, warm_units=None):
    """CP-SAT로 시간표를 풀어 (State, status_str)를 돌려준다. 실패 시 (None, status).

    progress(msg): 진행 메시지 콜백. 해를 찾을 때마다 현재 실제 페널티를 보냄.
    stop_event: threading.Event. set되면 탐색을 즉시 멈추고 그때까지의 최선해 반환.
    warm_units: 유닛 인덱스별 (day,period) 리스트. 있으면 이어서 돌리기(워밍스타트)로 사용.
    """
    mc = sch.max_consecutive
    m = cp_model.CpModel()

    place = {}
    for u in range(sch.n_units):
        vs = []
        for (d, p) in sch.unit_candidate_slots[u]:
            b = m.NewBoolVar(f"x{u}_{_sid(d,p)}")
            place[(u, _sid(d, p))] = b
            vs.append(b)
        m.AddExactlyOne(vs)

    class_units = defaultdict(set)
    teacher_units = defaultdict(set)
    usubj = defaultdict(lambda: defaultdict(set))
    csubj = defaultdict(set)
    cd_group = defaultdict(lambda: defaultdict(set))
    for u in range(sch.n_units):
        for (cid, tch, subj) in sch.units[u].cells:
            class_units[cid].add(u)
            teacher_units[tch].add(u)
            usubj[cid][subj].add(u)
            csubj[(cid, subj)].add(u)
            grp = sch.subject_to_group.get(subj)
            if grp:
                cd_group[cid][grp].add(u)

    # ── 하드: H2 / H3 / H8 ──
    for c, us in class_units.items():
        for s in range(35):
            vs = [place[(u, s)] for u in us if (u, s) in place]
            if len(vs) > 1:
                m.AddAtMostOne(vs)
    for t, us in teacher_units.items():
        for s in range(35):
            vs = [place[(u, s)] for u in us if (u, s) in place]
            if len(vs) > 1:
                m.AddAtMostOne(vs)
    for bk, sibs in sch.bundle_sibling.items():
        for day in range(5):
            vs = [place[(u, day * 7 + p)] for u in sibs for p in range(7)
                  if (u, day * 7 + p) in place]
            if len(vs) > 1:
                m.AddAtMostOne(vs)

    obj = []

    # H5: (학급,과목,요일) cap 초과 (소프트)
    for c, sm in usubj.items():
        for subj, us in sm.items():
            cap = sch.subj_day_cap.get((c, subj), 1)
            for d in DAYS:
                vs = [place[(u, _sid(d, p))] for u in us for p in range(1, 8)
                      if (u, _sid(d, p)) in place]
                if not vs:
                    continue
                ex = m.NewIntVar(0, 7, f"h5_{c}_{subj}_{d}")
                m.Add(ex >= sum(vs) - cap)
                obj.append(5 * PEN_SAME_DAY * ex)  # H5 가중 강화(소프트 유지로 첫해 확보)

    # teach 불리언
    tb = {}
    for t in teacher_units:
        for d in DAYS:
            for p in range(1, 8):
                vs = [place[(u, _sid(d, p))] for u in teacher_units[t]
                      if (u, _sid(d, p)) in place]
                if vs:
                    b = m.NewBoolVar(f"tb_{t}_{d}_{p}")
                    m.Add(b == sum(vs))
                    tb[(t, d, p)] = b
                else:
                    tb[(t, d, p)] = None

    # H7: (mc+1) 연속 초과 (소프트, 가중 강화로 우선 제거)
    for t in teacher_units:
        for d in DAYS:
            for p in range(1, 8 - mc):
                win = [tb[(t, d, q)] for q in range(p, p + mc + 1)
                       if tb.get((t, d, q)) is not None]
                if len(win) < mc + 1:
                    continue
                ov = m.NewIntVar(0, mc + 1, f"h7_{t}_{d}_{p}")
                m.Add(ov >= sum(win) - mc)
                obj.append(4 * PEN_CONSEC * ov)

    # S8: 하루 시수 초과 (소프트)
    for t, us in teacher_units.items():
        avg = sch.teacher_avg_daily.get(t, 5)
        for d in DAYS:
            vs = [tb[(t, d, p)] for p in range(1, 8) if tb.get((t, d, p)) is not None]
            if not vs:
                continue
            ex = m.NewIntVar(0, 7, f"s8_{t}_{d}")
            m.Add(ex >= sum(vs) - avg)
            obj.append(PEN_TEACHER_DAILY * ex)

    # H11: 2시간 과목 인접요일 (소프트)
    for (cid, subj), us in csubj.items():
        hrs = sum(1 for u in us for cell in sch.units[u].cells
                  if cell[0] == cid and cell[2] == subj)
        if hrs != 2:
            continue
        for di in range(4):
            d1, d2 = DAYS[di], DAYS[di + 1]
            v1 = [place[(u, _sid(d1, p))] for u in us for p in range(1, 8) if (u, _sid(d1, p)) in place]
            v2 = [place[(u, _sid(d2, p))] for u in us for p in range(1, 8) if (u, _sid(d2, p)) in place]
            if v1 and v2:
                hb = m.NewBoolVar(f"h11_{cid}_{subj}_{di}")
                m.Add(sum(v1) + sum(v2) - 1 <= hb)
                m.Add(hb <= sum(v1))
                m.Add(hb <= sum(v2))
                obj.append(PEN_2H_CONSEC_DAY * hb)

    # S9: 유사과목 같은 날 (소프트)
    for cid, groups in cd_group.items():
        for grp, us in groups.items():
            for d in DAYS:
                vs = [place[(u, _sid(d, p))] for u in us for p in range(1, 8)
                      if (u, _sid(d, p)) in place]
                if len(vs) > 1:
                    ex = m.NewIntVar(0, 7, f"s9_{cid}_{grp}_{d}")
                    m.Add(ex >= sum(vs) - 1)
                    obj.append(PEN_SIMILAR_SAME_DAY * ex)

    # 묶기(새 규칙, 경량): 하루 3시수↑인데 연강 한 쌍도 없으면 벌점
    for t in teacher_units:
        for d in DAYS:
            tbs = [tb[(t, d, p)] for p in range(1, 8) if tb.get((t, d, p)) is not None]
            if len(tbs) < 3:
                continue
            nday = m.NewIntVar(0, 7, f"n_{t}_{d}")
            m.Add(nday == sum(tbs))
            ge3 = m.NewBoolVar(f"ge3_{t}_{d}")
            m.Add(nday >= 3).OnlyEnforceIf(ge3)
            m.Add(nday <= 2).OnlyEnforceIf(ge3.Not())
            pairs = []
            for p in range(1, 7):
                a, b = tb.get((t, d, p)), tb.get((t, d, p + 1))
                if a is None or b is None:
                    continue
                y = m.NewBoolVar(f"pr_{t}_{d}_{p}")
                m.Add(y <= a); m.Add(y <= b); m.Add(y >= a + b - 1)
                pairs.append(y)
            if not pairs:
                continue
            frag = m.NewBoolVar(f"frag_{t}_{d}")
            m.Add(frag >= ge3 - sum(pairs))
            obj.append(PEN_FRAGMENT * frag)

    # 점심 전후 연속 (선택, 소프트)
    if sch.lunch_split:
        lp = sch.lunch_period
        for t in teacher_units:
            for d in DAYS:
                a, b = tb.get((t, d, lp)), tb.get((t, d, lp + 1))
                if a is None or b is None:
                    continue
                lb = m.NewBoolVar(f"lunch_{t}_{d}")
                m.Add(lb >= a + b - 1)
                obj.append(PEN_LUNCH_CROSS * lb)

    m.Minimize(sum(obj))

    # 워밍스타트: 이어서 돌리기면 저장된 해를 힌트로, 아니면 하드충족해를 힌트로
    if warm_units is not None:
        if progress:
            progress("이전 시간표에서 이어서 시작...")
        for u, dp in enumerate(warm_units):
            if dp is None:
                continue
            key = (u, _sid(dp[0], dp[1]))
            if key in place:
                m.AddHint(place[key], 1)
    else:
        if progress:
            progress("기본 배치 가능 여부 확인 중...")
        feas_status = _add_feasible_hint(sch, m, place, workers)
        # 기본 배치(충돌 없이 넣기)조차 불가능하면 더 풀 것도 없이 즉시 종료
        if feas_status == cp_model.INFEASIBLE:
            return None, "INFEASIBLE"

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit)
    solver.parameters.num_search_workers = workers
    if progress:
        progress("CP-SAT 최적화 중...")
    cb = _ProgressCb(sch, place, progress, stop_event)
    status = solver.Solve(m, cb)
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        st = State(sch)
        for u in range(sch.n_units):
            for (d, p) in sch.unit_candidate_slots[u]:
                if solver.Value(place[(u, _sid(d, p))]) == 1:
                    st._place(u, d, p)
                    break
        return st, solver.StatusName(status)
    return None, cp_model.CpSolver().StatusName(status)


class _ProgressCb(cp_model.CpSolverSolutionCallback):
    """해를 찾을 때마다 실제 페널티를 보고하고, 중단 요청을 처리한다."""

    def __init__(self, sch, place, progress, stop_event):
        super().__init__()
        self.sch = sch
        self.place = place
        self.progress = progress
        self.stop_event = stop_event
        self.n = 0
        self.best = None

    def on_solution_callback(self):
        try:
            self.n += 1
            st = State(self.sch)
            for u in range(self.sch.n_units):
                for (d, p) in self.sch.unit_candidate_slots[u]:
                    if self.Value(self.place[(u, _sid(d, p))]) == 1:
                        st._place(u, d, p)
                        break
            pen = st.get_solution().penalty
            self.best = pen
            if self.progress:
                self.progress(f"개선 중 — 현재 페널티 {pen} (해 {self.n}개 발견, 중단 가능)")
            if self.stop_event is not None and self.stop_event.is_set():
                self.StopSearch()
        except Exception:
            # 콜백 내부 예외가 ortools(C++)로 전파되면 프로세스가 죽으므로 여기서 차단
            if self.stop_event is not None and self.stop_event.is_set():
                try:
                    self.StopSearch()
                except Exception:
                    pass


def _add_feasible_hint(sch, m, place, workers):
    hm = cp_model.CpModel()
    hp = {}
    for u in range(sch.n_units):
        vs = []
        for (d, p) in sch.unit_candidate_slots[u]:
            b = hm.NewBoolVar(f"h{u}_{_sid(d,p)}")
            hp[(u, _sid(d, p))] = b
            vs.append(b)
        hm.AddExactlyOne(vs)
    cu = defaultdict(set)
    tu = defaultdict(set)
    for u in range(sch.n_units):
        for (cid, tch, subj) in sch.units[u].cells:
            cu[cid].add(u)
            tu[tch].add(u)
    for c, us in cu.items():
        for s in range(35):
            vs = [hp[(u, s)] for u in us if (u, s) in hp]
            if len(vs) > 1:
                hm.AddAtMostOne(vs)
    for t, us in tu.items():
        for s in range(35):
            vs = [hp[(u, s)] for u in us if (u, s) in hp]
            if len(vs) > 1:
                hm.AddAtMostOne(vs)
    for bk, sibs in sch.bundle_sibling.items():
        for day in range(5):
            vs = [hp[(u, day * 7 + p)] for u in sibs for p in range(7) if (u, day * 7 + p) in hp]
            if len(vs) > 1:
                hm.AddAtMostOne(vs)
    hs = cp_model.CpSolver()
    hs.parameters.max_time_in_seconds = 30
    hs.parameters.num_search_workers = workers
    status = hs.Solve(hm)
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for (u, s), var in place.items():
            if (u, s) in hp:
                m.AddHint(var, hs.Value(hp[(u, s)]))
    return status
