"""학교 시간표 생성기 v7.4.0 - 2단계(Bilevel) 탐색

설계 (v6 → v7):
  ★ 묶음(골격)과 낱개(충전)를 계층 분리.
      - 묶음 슬롯은 자리잡기 가장 어려움 → 바깥 루프에서만 흔든다.
      - 낱개 수업은 빈틈에 유연 → 안쪽 루프에서 빠르게 충전.
  ★ "무질서도 증가(reheat)" = 묶음 골격을 재배치하고 그 위에 낱개를 다시 채움.
      정체 시 흔드는 묶음 슬롯 수(k)를 키워 더 큰 무질서.
  ★ 묶음 시수는 서로 다른 요일에 분산 (형제 슬롯 동일요일 금지)
      → 학급-과목 하루 1회(H5) 위반이 묶음에서 구조적으로 0.

유지(v6): 원자적 배치 단위(묶음 분리 불가), 증분 델타 O(1), Tabu.
기본값: 최대 연속 허용 시수(H7) 기본 3 → 2.
"""
import math, random, time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Callable

DAYS = ["월", "화", "수", "목", "금"]
DAY_IDX = {d: i for i, d in enumerate(DAYS)}
PERIODS = list(range(1, 8))

PEN_CLASS_CONFLICT=1000; PEN_TEACHER_CONFLICT=1000; PEN_NONCLASS=1000
PEN_SAME_DAY=100; PEN_UNAVAIL=1000; PEN_CONSEC=50; PEN_BUNDLE_OVERLAP=1000
PEN_2H_CONSEC_DAY=30; PEN_TEACHER_DAILY=10; PEN_SIMILAR_SAME_DAY=5
PEN_FRAGMENT=25   # 교사 하루 3시수 이상인데 연강(2개 묶음)이 하나도 없을 때 1회
PEN_LUNCH_CROSS=40  # (선택) 한 교사가 점심 직전·직후 교시를 연달아 맡을 때
_EMPTY = frozenset()


@dataclass
class Solution:
    assignments: List[Tuple[str, str, str, str, int, str]] = field(default_factory=list)
    penalty: int = 0
    violations: Dict[str, int] = field(default_factory=dict)


@dataclass
class PlacementUnit:
    uid: int
    kind: str
    cells: Tuple[Tuple[str, str, str], ...]
    bundle_key: str = ""


def _all_slots():
    return [(d, p) for d in DAYS for p in PERIODS]


class State:
    def __init__(self, scheduler):
        self.sch = scheduler
        self.pos = [None] * len(scheduler.units)
        self.penalty = 0
        self.class_slot = defaultdict(int)
        self.teacher_slot = defaultdict(int)
        self.class_subj_day = defaultdict(int)
        self.teacher_day = defaultdict(int)
        self.class_day_group = defaultdict(int)
        self.sync_slot = defaultdict(int)
        self.teacher_day_periods = defaultdict(set)   # (교사,요일) -> {교시}
        self.h7_pen = defaultdict(int)                # (교사,요일) -> H7 페널티
        self.class_subj_dp = defaultdict(list)
        self.h11_pen = defaultdict(int)
        self.s8_pen = defaultdict(int)

    def _tday_parts(self, periods, mc):
        """교사 하루 교시 패턴 → (H7 연속초과 페널티, 묶기 위반 페널티).
        - H7: 연속 길이가 mc 초과면 벌점.
        - 묶기: 하루 3시수 이상이면 '최소 한 쌍'(2개 연강)이 있어야 함.
          연강(길이≥2 블록)이 하나도 없이 전부 흩어진 경우에만 벌점 1회.
          (예: 4시수가 2+2가 아니라 2+1+1이어도, 연강 한 쌍이 있으니 위반 아님)
        """
        n = len(periods)
        if n == 0:
            return 0, 0
        runs = []; cur = 1
        for i in range(1, n):
            if periods[i] == periods[i-1] + 1:
                cur += 1
            else:
                runs.append(cur); cur = 1
        runs.append(cur)
        h7 = sum(PEN_CONSEC * (r - mc) for r in runs if r > mc)
        frag = 0
        if n >= 3 and not any(r >= 2 for r in runs):
            frag = PEN_FRAGMENT
        return h7, frag

    def _h7(self, tch, d):
        periods = sorted(self.teacher_day_periods[(tch, d)])
        h7, frag = self._tday_parts(periods, self.sch.max_consecutive)
        lunch = 0
        if self.sch.lunch_split:
            lp = self.sch.lunch_period
            ps = self.teacher_day_periods[(tch, d)]
            if lp in ps and (lp + 1) in ps:
                lunch = PEN_LUNCH_CROSS
        return h7 + frag + lunch

    def _h11(self, cid, subj):
        dplist = self.class_subj_dp[(cid, subj)]
        if len(dplist) != 2:
            return 0
        days = {d for d, _ in dplist}
        if len(days) != 2:
            return 0
        idxs = sorted(DAY_IDX[d] for d in days)
        return PEN_2H_CONSEC_DAY if idxs[1] - idxs[0] == 1 else 0

    def _s8(self, tch, d):
        cnt = self.teacher_day[(tch, d)]
        avg = self.sch.teacher_avg_daily.get(tch, 5)
        return PEN_TEACHER_DAILY * (cnt - avg) if cnt > avg else 0

    def _place(self, uid, d, p):
        unit = self.sch.units[uid]; delta = 0
        for (cid, tch, subj) in unit.cells:
            g = self.sch.class_grade[cid]
            if (g, d, p) in self.sch.nonclass_set: delta += PEN_NONCLASS
            if (d, p) in self.sch.unavail_set.get(tch, _EMPTY): delta += PEN_UNAVAIL
            if self.class_slot[(cid, d, p)] >= 1: delta += PEN_CLASS_CONFLICT
            self.class_slot[(cid, d, p)] += 1
            if self.teacher_slot[(tch, d, p)] >= 1: delta += PEN_TEACHER_CONFLICT
            self.teacher_slot[(tch, d, p)] += 1
            cap = self.sch.subj_day_cap.get((cid, subj), 1)
            if self.class_subj_day[(cid, subj, d)] >= cap: delta += PEN_SAME_DAY
            self.class_subj_day[(cid, subj, d)] += 1
            o = self.h7_pen[(tch, d)]; self.teacher_day_periods[(tch, d)].add(p)
            n = self._h7(tch, d); self.h7_pen[(tch, d)] = n; delta += n - o
            o = self.h11_pen[(cid, subj)]; self.class_subj_dp[(cid, subj)].append((d, p))
            n = self._h11(cid, subj); self.h11_pen[(cid, subj)] = n; delta += n - o
            o = self.s8_pen[(tch, d)]; self.teacher_day[(tch, d)] += 1
            n = self._s8(tch, d); self.s8_pen[(tch, d)] = n; delta += n - o
            grp = self.sch.subject_to_group.get(subj)
            if grp:
                if self.class_day_group[(cid, d, grp)] >= 1: delta += PEN_SIMILAR_SAME_DAY
                self.class_day_group[(cid, d, grp)] += 1
        if unit.bundle_key:
            if self.sync_slot[(unit.bundle_key, d, p)] >= 1: delta += PEN_BUNDLE_OVERLAP
            self.sync_slot[(unit.bundle_key, d, p)] += 1
        self.pos[uid] = (d, p); self.penalty += delta
        return delta

    def _unplace(self, uid):
        dp = self.pos[uid]
        if dp is None: return 0
        d, p = dp; unit = self.sch.units[uid]; delta = 0
        for (cid, tch, subj) in unit.cells:
            g = self.sch.class_grade[cid]
            if (g, d, p) in self.sch.nonclass_set: delta -= PEN_NONCLASS
            if (d, p) in self.sch.unavail_set.get(tch, _EMPTY): delta -= PEN_UNAVAIL
            c = self.class_slot[(cid, d, p)]
            if c >= 2: delta -= PEN_CLASS_CONFLICT
            if c == 1: del self.class_slot[(cid, d, p)]
            else: self.class_slot[(cid, d, p)] = c - 1
            c = self.teacher_slot[(tch, d, p)]
            if c >= 2: delta -= PEN_TEACHER_CONFLICT
            if c == 1: del self.teacher_slot[(tch, d, p)]
            else: self.teacher_slot[(tch, d, p)] = c - 1
            const_cap = self.sch.subj_day_cap.get((cid, subj), 1)
            c = self.class_subj_day[(cid, subj, d)]
            if c >= const_cap + 1: delta -= PEN_SAME_DAY
            if c == 1: del self.class_subj_day[(cid, subj, d)]
            else: self.class_subj_day[(cid, subj, d)] = c - 1
            o = self.h7_pen[(tch, d)]
            if self.teacher_slot.get((tch, d, p), 0) == 0:
                self.teacher_day_periods[(tch, d)].discard(p)
                if not self.teacher_day_periods[(tch, d)]: del self.teacher_day_periods[(tch, d)]
            n = self._h7(tch, d) if (tch, d) in self.teacher_day_periods else 0
            if n: self.h7_pen[(tch, d)] = n
            elif (tch, d) in self.h7_pen: del self.h7_pen[(tch, d)]
            delta += n - o
            o = self.h11_pen[(cid, subj)]; lst = self.class_subj_dp[(cid, subj)]
            try: lst.remove((d, p))
            except ValueError: pass
            if not lst: del self.class_subj_dp[(cid, subj)]
            n = self._h11(cid, subj)
            if n: self.h11_pen[(cid, subj)] = n
            elif (cid, subj) in self.h11_pen: del self.h11_pen[(cid, subj)]
            delta += n - o
            o = self.s8_pen[(tch, d)]; self.teacher_day[(tch, d)] -= 1
            if self.teacher_day[(tch, d)] == 0: del self.teacher_day[(tch, d)]
            n = self._s8(tch, d)
            if n: self.s8_pen[(tch, d)] = n
            elif (tch, d) in self.s8_pen: del self.s8_pen[(tch, d)]
            delta += n - o
            grp = self.sch.subject_to_group.get(subj)
            if grp:
                c = self.class_day_group[(cid, d, grp)]
                if c >= 2: delta -= PEN_SIMILAR_SAME_DAY
                if c == 1: del self.class_day_group[(cid, d, grp)]
                else: self.class_day_group[(cid, d, grp)] = c - 1
        if unit.bundle_key:
            c = self.sync_slot[(unit.bundle_key, d, p)]
            if c >= 2: delta -= PEN_BUNDLE_OVERLAP
            if c == 1: del self.sync_slot[(unit.bundle_key, d, p)]
            else: self.sync_slot[(unit.bundle_key, d, p)] = c - 1
        self.pos[uid] = None; self.penalty += delta
        return delta

    def move(self, uid, d, p):
        return self._unplace(uid) + self._place(uid, d, p)

    def swap(self, uid1, uid2):
        """두 단위의 (요일,교시)를 맞바꾼다. 가역적."""
        dp1 = self.pos[uid1]; dp2 = self.pos[uid2]
        if dp1 is None or dp2 is None or dp1 == dp2:
            return 0
        before = self.penalty
        self.move(uid1, dp2[0], dp2[1])
        self.move(uid2, dp1[0], dp1[1])
        return self.penalty - before

    def evaluate_place(self, uid, d, p):
        old = self.pos[uid]
        if old is not None:
            before = self.penalty
            self.move(uid, d, p)
            delta = self.penalty - before
            self.move(uid, old[0], old[1])   # 원위치 복원 (move는 가역적)
            return delta
        delta = self._place(uid, d, p); self._unplace(uid)
        return delta

    def get_solution(self):
        assignments = []
        for uid, unit in enumerate(self.sch.units):
            dp = self.pos[uid]
            if dp is None: continue
            d, p = dp
            for (cid, tch, subj) in unit.cells:
                assignments.append((subj, tch, cid, d, p, unit.bundle_key))
        sol = Solution(assignments=assignments); sol.penalty = self.penalty
        v = defaultdict(int)
        for c in self.class_slot.values():
            if c > 1: v["H2"] += c - 1
        for c in self.teacher_slot.values():
            if c > 1: v["H3"] += c - 1
        for (cid, subj, d), c in self.class_subj_day.items():
            cap = self.sch.subj_day_cap.get((cid, subj), 1)
            if c > cap: v["H5"] += c - cap
        mc = self.sch.max_consecutive
        for (tch, d), ps in self.teacher_day_periods.items():
            h7p, fragp = self._tday_parts(sorted(ps), mc)
            if h7p > 0: v["H7"] += 1
            if fragp > 0: v["Hpair"] += 1
            if self.sch.lunch_split:
                lp = self.sch.lunch_period
                if lp in ps and (lp + 1) in ps: v["Lunch"] += 1
        for pen in self.h11_pen.values():
            if pen > 0: v["H11"] += 1
        for pen in self.s8_pen.values():
            if pen > 0: v["S8"] += 1
        for c in self.class_day_group.values():
            if c > 1: v["S9"] += c - 1
        for c in self.sync_slot.values():
            if c > 1: v["H8"] += c - 1
        for uid, unit in enumerate(self.sch.units):
            dp = self.pos[uid]
            if dp is None: continue
            d, p = dp
            for (cid, tch, subj) in unit.cells:
                g = self.sch.class_grade[cid]
                if (g, d, p) in self.sch.nonclass_set: v["H4"] += 1
                if (d, p) in self.sch.unavail_set.get(tch, _EMPTY): v["H6"] += 1
        sol.violations = dict(v)
        return sol

    def snapshot(self):
        return list(self.pos)

    def restore(self, snap):
        for d in (self.class_slot, self.teacher_slot, self.class_subj_day,
                  self.teacher_day, self.class_day_group, self.sync_slot,
                  self.teacher_day_periods, self.h7_pen, self.class_subj_dp,
                  self.h11_pen, self.s8_pen):
            d.clear()
        self.pos = [None] * len(self.sch.units); self.penalty = 0
        for uid, dp in enumerate(snap):
            if dp is not None:
                self._place(uid, dp[0], dp[1])


class TabuList:
    def __init__(self, maxlen):
        self.maxlen = maxlen; self.dq = deque(); self.s = set()
    def add(self, key):
        if key in self.s: return
        if len(self.dq) >= self.maxlen: self.s.discard(self.dq.popleft())
        self.dq.append(key); self.s.add(key)
    def __contains__(self, key):
        return key in self.s


class HybridScheduler:
    def __init__(self, data, non_class, unavail, similar, params):
        self.data = data; self.non_class = non_class; self.unavail = unavail
        self.similar = similar; self.params = params
        self.max_consecutive = params.get("max_consecutive", 2)
        self.daily_n = params.get("daily_n", 1)
        # 점심 전후 연속수업 방지(선택): lunch_split=True면 점심 직전 교시와
        # 직후 교시를 한 교사가 연달아 맡을 때 벌점. lunch_period = 점심 직전 교시.
        self.lunch_split = bool(params.get("lunch_split", False))
        self.lunch_period = params.get("lunch_period", 4)

        self.all_classes = []
        for g, cls in data.classes_per_grade.items():
            self.all_classes.extend(cls)
        for sr in data.special_rooms:
            cid = f"{sr.grade}-{sr.code}"
            if cid not in self.all_classes: self.all_classes.append(cid)
        self.all_classes = sorted(set(self.all_classes),
                                  key=lambda c: (int(c.split("-")[0]), c.split("-")[1]))
        self.class_grade = {cid: int(cid.split("-")[0]) for cid in self.all_classes}

        self.nonclass_set = set((s.grade, s.day, s.period) for s in non_class)
        self.unavail_set = defaultdict(set)
        for u in unavail:
            self.unavail_set[u.teacher].add((u.day, u.period))
        self.subject_to_group = {}
        for g in similar:
            for s in g.subjects:
                self.subject_to_group[s] = g.name

        from excel_parser import compute_teacher_total_hours
        teacher_tot = compute_teacher_total_hours(data)
        self.teacher_avg_daily = {t: round(tot/5) + self.daily_n for t, tot in teacher_tot.items()}

        self.units = []; uid = 0
        for a in data.fixed_assignments:
            self.class_grade.setdefault(a.class_id, int(a.class_id.split("-")[0]))
            for _ in range(a.hours):
                self.units.append(PlacementUnit(uid, 'fixed', ((a.class_id, a.teacher, a.subject),), ""))
                uid += 1
        self.bundle_keys = []
        for bg in data.bundle_groups:
            bkey = f"{bg.grade}_{bg.code}"; self.bundle_keys.append(bkey)
            class_members = defaultdict(list)
            for m in bg.members:
                class_members[m.class_id].append((m.subject, m.teacher, m.hours))
                self.class_grade.setdefault(m.class_id, int(m.class_id.split("-")[0]))
            for slot_idx in range(bg.hours):
                cells = []
                for cid, mlist in class_members.items():
                    cum = 0; chosen = None
                    for subj, tch, h in mlist:
                        if cum <= slot_idx < cum + h: chosen = (cid, tch, subj); break
                        cum += h
                    if chosen is None and mlist:
                        subj, tch, h = mlist[-1]; chosen = (cid, tch, subj)
                    if chosen: cells.append(chosen)
                self.units.append(PlacementUnit(uid, 'bundle', tuple(cells), bkey))
                uid += 1

        self.n_units = len(self.units)
        self._all_slots_cache = _all_slots()
        self.fixed_uids = [u for u in range(self.n_units) if not self.units[u].bundle_key]
        self.bundle_uids = [u for u in range(self.n_units) if self.units[u].bundle_key]
        self.bundle_sibling = defaultdict(list)
        for u in self.bundle_uids:
            self.bundle_sibling[self.units[u].bundle_key].append(u)

        # 교사 → 담당 낱개 단위 (교사 중심 공략용)
        self.teacher_units = defaultdict(list)
        for u in self.fixed_uids:
            self.teacher_units[self.units[u].cells[0][1]].append(u)

        # (학급,과목) 하루 허용 횟수 cap = ceil(주당시수/5)
        # 주 5시간 초과 과목은 어떤 날 2회가 불가피 → 그만큼은 H5 위반으로 치지 않음
        import math as _m
        subj_total = defaultdict(int)
        for a in data.fixed_assignments:
            subj_total[(a.class_id, a.subject)] += a.hours
        for bg in data.bundle_groups:
            for mm in bg.members:
                subj_total[(mm.class_id, mm.subject)] += mm.hours
        self.subj_day_cap = {}
        for (cid, subj), tot in subj_total.items():
            self.subj_day_cap[(cid, subj)] = max(1, _m.ceil(tot / 5))

        self.unit_candidate_slots = []
        for unit in self.units:
            cands = []
            for d, p in self._all_slots_cache:
                ok = True
                for (cid, tch, subj) in unit.cells:
                    g = self.class_grade[cid]
                    if (g, d, p) in self.nonclass_set or (d, p) in self.unavail_set.get(tch, _EMPTY):
                        ok = False; break
                if ok: cands.append((d, p))
            self.unit_candidate_slots.append(cands if cands else list(self._all_slots_cache))

    def _bundle_legal_slots(self, state, uid):
        bkey = self.units[uid].bundle_key
        used = set()
        for sib in self.bundle_sibling[bkey]:
            if sib != uid and state.pos[sib] is not None:
                used.add(state.pos[sib][0])
        legal = [(d, p) for (d, p) in self.unit_candidate_slots[uid] if d not in used]
        return legal if legal else self.unit_candidate_slots[uid]

    def smart_initial(self):
        state = State(self)
        for uid in sorted(self.bundle_uids, key=lambda u: -len(self.units[u].cells)):
            cands = list(self._bundle_legal_slots(state, uid)); random.shuffle(cands)
            best_slot, best_delta = None, 1 << 30
            for d, p in cands:
                delta = state.evaluate_place(uid, d, p)
                if delta < best_delta:
                    best_delta, best_slot = delta, (d, p)
                    if delta == 0: break
            if best_slot is None:
                best_slot = random.choice(cands) if cands else random.choice(self._all_slots_cache)
            state._place(uid, best_slot[0], best_slot[1])
        forder = list(self.fixed_uids); random.shuffle(forder)
        for uid in forder:
            cands = list(self.unit_candidate_slots[uid]); random.shuffle(cands)
            best_slot, best_delta = None, 1 << 30
            for d, p in cands:
                delta = state.evaluate_place(uid, d, p)
                if delta < best_delta:
                    best_delta, best_slot = delta, (d, p)
                    if delta == 0: break
            if best_slot is None: best_slot = random.choice(self._all_slots_cache)
            state._place(uid, best_slot[0], best_slot[1])
        return state

    def initial_solution(self):
        return self.smart_initial().get_solution()

    def evaluate(self, sol):
        st = self._state_from_solution(sol)
        sol.penalty = st.penalty
        sol.violations = st.get_solution().violations

    def _state_from_solution(self, sol):
        state = State(self)
        bundle_dp = defaultdict(lambda: defaultdict(int))
        for (subj, tch, cid, d, p, bk) in sol.assignments:
            if bk: bundle_dp[bk][(d, p)] += 1
        for bkey, uids in self.bundle_sibling.items():
            slot_size = len(self.units[uids[0]].cells)
            dps = sorted(bundle_dp.get(bkey, {}).items(), key=lambda kv: -kv[1])
            slot_dps = []
            for dp, c in dps:
                slot_dps.extend([dp] * max(1, round(c / max(slot_size, 1))))
            i = 0
            for uid in uids:
                if i < len(slot_dps): d, p = slot_dps[i]; i += 1
                else:
                    cands = self._bundle_legal_slots(state, uid); d, p = random.choice(cands)
                state._place(uid, d, p)
        pool = defaultdict(list)
        for (subj, tch, cid, d, p, bk) in sol.assignments:
            if not bk: pool[(cid, tch, subj)].append((d, p))
        for uid in self.fixed_uids:
            (cid, tch, subj) = self.units[uid].cells[0]
            if pool.get((cid, tch, subj)): d, p = pool[(cid, tch, subj)].pop()
            else:
                cands = list(self.unit_candidate_slots[uid]); random.shuffle(cands)
                best_slot, best_delta = None, 1 << 30
                for dd, pp in cands:
                    delta = state.evaluate_place(uid, dd, pp)
                    if delta < best_delta:
                        best_delta, best_slot = delta, (dd, pp)
                        if delta == 0: break
                d, p = best_slot if best_slot else random.choice(self._all_slots_cache)
            state._place(uid, d, p)
        return state

    def local_search(self, state, n_iter, T_start, T_end=0.5, tabu_size=30,
                     reheat_stagnation=1500, bundle_move_prob=0.12,
                     teacher_focus_prob=0.18):
        """통합 고속 탐색: 낱개·묶음 이동 + 교사중심 교체(swap).
        - 교사별 위반 점수를 매겨, 점수 높은 교사의 수업을 우선 교체.
        - 정체 시 계층적 reheat (묶음 골격 흔들기).
        """
        if self.n_units == 0: return state
        cooling = (T_end / T_start) ** (1.0 / n_iter) if (T_start > T_end and n_iter > 1) else 0.9995
        T = T_start; best_pen = state.penalty; best_snap = state.snapshot()
        tabu = TabuList(tabu_size); stagn = 0
        tb = self.teacher_badness(state); refresh = 0
        for _ in range(n_iter):
            refresh += 1
            if refresh >= 120:   # 교사 점수표 주기적 갱신
                tb = self.teacher_badness(state); refresh = 0

            roll = random.random()
            did_swap = None  # (uid1, uid2) when a swap was applied

            if self.bundle_uids and roll < bundle_move_prob:
                uid = random.choice(self.bundle_uids)
                nd, np_ = random.choice(self._bundle_legal_slots(state, uid))
                old_dp = state.pos[uid]
                if (nd, np_) == old_dp: continue
                is_tabu = (uid, nd, np_) in tabu
                delta = state.move(uid, nd, np_)
                accept = (delta < 0) or (state.penalty < best_pen) or \
                         (not is_tabu and T > 1e-6 and random.random() < math.exp(-min(delta, 700) / T))
                if accept:
                    tabu.add((uid, old_dp[0], old_dp[1]))
                else:
                    state.move(uid, old_dp[0], old_dp[1])

            elif state.penalty > 0 and roll < bundle_move_prob + teacher_focus_prob:
                action = self._teacher_focused(state, tb)
                if action is None:
                    uid = random.choice(self.fixed_uids)
                    nd, np_ = random.choice(self.unit_candidate_slots[uid])
                    old_dp = state.pos[uid]
                    if (nd, np_) == old_dp: continue
                    delta = state.move(uid, nd, np_)
                    accept = (delta < 0) or (state.penalty < best_pen) or \
                             (T > 1e-6 and random.random() < math.exp(-min(delta, 700) / T))
                    if not accept: state.move(uid, old_dp[0], old_dp[1])
                elif action[0] == 'swap':
                    _, u1, u2 = action
                    before = state.penalty
                    state.swap(u1, u2)
                    delta = state.penalty - before
                    accept = (delta < 0) or (state.penalty < best_pen) or \
                             (T > 1e-6 and random.random() < math.exp(-min(delta, 700) / T))
                    if not accept: state.swap(u1, u2)
                else:
                    _, uid, (nd, np_) = action
                    old_dp = state.pos[uid]
                    delta = state.move(uid, nd, np_)
                    accept = (delta < 0) or (state.penalty < best_pen) or \
                             (T > 1e-6 and random.random() < math.exp(-min(delta, 700) / T))
                    if accept: tabu.add((uid, old_dp[0], old_dp[1]))
                    else: state.move(uid, old_dp[0], old_dp[1])

            else:
                if state.penalty > 0 and random.random() < 0.7:
                    uid = self._pick_critical_fixed(state)
                    if uid is None: uid = random.choice(self.fixed_uids)
                else:
                    uid = random.choice(self.fixed_uids)
                old_dp = state.pos[uid]
                nd, np_ = random.choice(self.unit_candidate_slots[uid])
                if (nd, np_) == old_dp: continue
                is_tabu = (uid, nd, np_) in tabu
                delta = state.move(uid, nd, np_)
                accept = (delta < 0) or (state.penalty < best_pen) or \
                         (not is_tabu and T > 1e-6 and random.random() < math.exp(-min(delta, 700) / T))
                if accept:
                    tabu.add((uid, old_dp[0], old_dp[1]))
                else:
                    state.move(uid, old_dp[0], old_dp[1])

            if state.penalty < best_pen:
                best_pen = state.penalty; best_snap = state.snapshot(); stagn = 0
            else:
                stagn += 1
            T *= cooling
            if stagn >= reheat_stagnation:
                state.restore(best_snap)
                k = random.randint(1, max(1, len(self.bundle_uids) // 3))
                self.perturb_bundles(state, k)
                T = T_start * 0.6; stagn = 0
                tb = self.teacher_badness(state); refresh = 0
            if state.penalty == 0: break
        if state.penalty != best_pen: state.restore(best_snap)
        return state

    def _unit_badness(self, state, uid):
        """이 단위가 현재 얽혀 있는 위반의 합 (저렴한 추정)."""
        dp = state.pos[uid]
        if dp is None: return 0
        d, p = dp; b = 0
        for (cid, tch, subj) in self.units[uid].cells:
            g = self.class_grade[cid]
            if (g, d, p) in self.nonclass_set: b += PEN_NONCLASS
            if (d, p) in self.unavail_set.get(tch, _EMPTY): b += PEN_UNAVAIL
            if state.class_slot.get((cid, d, p), 0) > 1: b += PEN_CLASS_CONFLICT
            if state.teacher_slot.get((tch, d, p), 0) > 1: b += PEN_TEACHER_CONFLICT
            if state.class_subj_day.get((cid, subj, d), 0) > self.subj_day_cap.get((cid, subj), 1): b += PEN_SAME_DAY
            b += state.h7_pen.get((tch, d), 0)
            b += state.h11_pen.get((cid, subj), 0)
            b += state.s8_pen.get((tch, d), 0)
        return b

    def teacher_badness(self, state):
        """교사별 위반 점수 합. {교사: 점수} (점수>0만)."""
        bad = defaultdict(int)
        for uid in self.fixed_uids:
            if state.pos[uid] is None: continue
            b = self._unit_badness(state, uid)
            if b:
                bad[self.units[uid].cells[0][1]] += b
        return bad

    def _teacher_focused(self, state, tb):
        """위반이 몰린 교사를 가중 추출 → 그 교사의 최악 수업을 교체/이동."""
        if not tb: return None
        teachers = list(tb.keys()); weights = list(tb.values())
        tot = sum(weights)
        if tot <= 0: return None
        r = random.uniform(0, tot); acc = 0; pick = teachers[-1]
        for t, w in zip(teachers, weights):
            acc += w
            if acc >= r: pick = t; break
        units_t = [u for u in self.teacher_units.get(pick, []) if state.pos[u] is not None]
        if not units_t: return None
        uid = max(units_t, key=lambda u: self._unit_badness(state, u))
        old = state.pos[uid]
        cands = self.unit_candidate_slots[uid]
        sample = random.sample(cands, min(len(cands), 15))
        best_slot, best_delta = None, 1 << 30
        for d, p in sample:
            delta = state.evaluate_place(uid, d, p)
            if delta < best_delta: best_delta, best_slot = delta, (d, p)
        if best_slot is None or best_slot == old:
            return None
        occ = None
        for u2 in self.fixed_uids:
            if u2 != uid and state.pos[u2] == best_slot:
                occ = u2; break
        if occ is not None and random.random() < 0.6:
            return ('swap', uid, occ)
        return ('move', uid, best_slot)

    def _pick_critical_fixed(self, state):
        cands = []
        for (cid, d, p), c in state.class_slot.items():
            if c > 1: cands.append(('cls', cid, d, p))
        for (tch, d, p), c in state.teacher_slot.items():
            if c > 1: cands.append(('tch', tch, d, p))
        for (cid, subj, d), c in state.class_subj_day.items():
            if c > self.subj_day_cap.get((cid, subj), 1): cands.append(('csd', cid, subj, d))
        for (tch, d), pen in state.h7_pen.items():
            if pen > 0: cands.append(('h7', tch, d))
        if not cands:
            crit = []
            for uid in self.fixed_uids:
                dp = state.pos[uid]
                if dp is None: continue
                d, p = dp; (cid, tch, subj) = self.units[uid].cells[0]
                g = self.class_grade[cid]
                if (g, d, p) in self.nonclass_set or (d, p) in self.unavail_set.get(tch, _EMPTY):
                    crit.append(uid)
            return random.choice(crit) if crit else None
        v = random.choice(cands); matching = []
        if v[0] == 'cls':
            _, cid, d, p = v
            for uid in self.fixed_uids:
                if state.pos[uid] == (d, p) and self.units[uid].cells[0][0] == cid: matching.append(uid)
        elif v[0] == 'tch':
            _, tch, d, p = v
            for uid in self.fixed_uids:
                if state.pos[uid] == (d, p) and self.units[uid].cells[0][1] == tch: matching.append(uid)
        elif v[0] == 'csd':
            _, cid, subj, d = v
            for uid in self.fixed_uids:
                dp = state.pos[uid]; c0 = self.units[uid].cells[0]
                if dp and dp[0] == d and c0[0] == cid and c0[2] == subj: matching.append(uid)
        elif v[0] == 'h7':
            _, tch, d = v
            for uid in self.fixed_uids:
                dp = state.pos[uid]
                if dp and dp[0] == d and self.units[uid].cells[0][1] == tch: matching.append(uid)
        return random.choice(matching) if matching else None

    def perturb_bundles(self, state, k):
        if not self.bundle_uids: return
        k = min(k, len(self.bundle_uids))
        for uid in random.sample(self.bundle_uids, k):
            legal = self._bundle_legal_slots(state, uid)
            if random.random() < 0.5:
                cand = list(legal); random.shuffle(cand)
                best_slot, best_delta = None, 1 << 30
                for d, p in cand[:12]:
                    delta = state.evaluate_place(uid, d, p)
                    if delta < best_delta: best_delta, best_slot = delta, (d, p)
                d, p = best_slot if best_slot else random.choice(legal)
            else:
                d, p = random.choice(legal)
            state.move(uid, d, p)

    def polish_pairing(self, state, rounds=4):
        """교사 흩어짐(묶기 위반) 전용 하강. 고립된 단독 수업을 같은 교사의
        다른 블록 옆으로 붙인다. 총 페널티가 줄 때만(=H7 증가 없이) 적용."""
        mc = self.max_consecutive
        for _ in range(rounds):
            improved = False
            frag_days = []
            for (tch, d), ps in list(state.teacher_day_periods.items()):
                _, frag = state._tday_parts(sorted(ps), mc)
                if frag > 0:
                    frag_days.append((tch, d))
            random.shuffle(frag_days)
            for (tch, d) in frag_days:
                ps = sorted(state.teacher_day_periods.get((tch, d), []))
                if len(ps) < 3:
                    continue
                singles = [p for p in ps if (p - 1 not in ps) and (p + 1 not in ps)]
                block_adj = set()
                for p in ps:
                    if (p - 1 in ps) or (p + 1 in ps):
                        if 1 <= p - 1 <= 7 and (p - 1) not in ps: block_adj.add(p - 1)
                        if 1 <= p + 1 <= 7 and (p + 1) not in ps: block_adj.add(p + 1)
                for sp in singles:
                    cid0 = None; uid = None
                    for u in self.fixed_uids:
                        if state.pos[u] == (d, sp) and self.units[u].cells[0][1] == tch:
                            uid = u; cid0 = self.units[u].cells[0][0]; break
                    if uid is None:
                        continue
                    for tp in block_adj:
                        before = state.penalty
                        if state.class_slot.get((cid0, d, tp), 0) == 0:
                            state.move(uid, d, tp)
                            if state.penalty < before:
                                improved = True; break
                            state.move(uid, d, sp)
                        else:
                            u2 = None
                            for u in self.fixed_uids:
                                if state.pos[u] == (d, tp) and self.units[u].cells[0][0] == cid0:
                                    u2 = u; break
                            if u2 is not None:
                                state.swap(uid, u2)
                                if state.penalty < before:
                                    improved = True; break
                                state.swap(uid, u2)
            if not improved:
                break
        return state

    def _best_slot_for(self, state, uid, free_only=True):
        old = state.pos[uid]; isb = bool(self.units[uid].bundle_key)
        cand = self._bundle_legal_slots(state, uid) if isb else self.unit_candidate_slots[uid]
        cid = self.units[uid].cells[0][0]
        best = None; bd = 1 << 30
        for d, p in cand:
            if old and (d, p) == old: continue
            if free_only and not isb and state.class_slot.get((cid, d, p), 0) > 0: continue
            delta = state.evaluate_place(uid, d, p)
            if delta < bd: bd = delta; best = (d, p)
        return best, bd

    def ejection_chain(self, state, uid, target, max_depth=5):
        """uid를 target으로 보내고, 밀려난 수업을 연쇄적으로 좋은 빈칸으로 이동.
        전체 페널티가 줄면 채택, 아니면 원복."""
        before = state.penalty; snap = state.snapshot()
        cur = uid; tgt = target
        for _ in range(max_depth):
            isb = bool(self.units[cur].bundle_key)
            cid = self.units[cur].cells[0][0] if not isb else None
            d, p = tgt; occ = None
            if cid is not None:
                for u2 in self.fixed_uids:
                    if u2 != cur and state.pos[u2] == (d, p) and self.units[u2].cells[0][0] == cid:
                        occ = u2; break
            state.move(cur, d, p)
            if occ is None: break
            cur = occ
            nb, _ = self._best_slot_for(state, occ, free_only=True)
            if nb is None: break
            tgt = nb
        if state.penalty < before:
            return True
        state.restore(snap); return False

    def chain_repair(self, state, passes=8, chain_tries=20):
        """위반 단위마다: 최선 이동 → 안 되면 연쇄 교환으로 자리 비틀기."""
        for _ in range(passes):
            improved = False
            targets = [u for u in range(self.n_units)
                       if state.pos[u] and self._unit_badness(state, u) > 0]
            targets.sort(key=lambda u: -self._unit_badness(state, u))
            for uid in targets:
                if not (state.pos[uid] and self._unit_badness(state, uid) > 0):
                    continue
                b, bd = self._best_slot_for(state, uid, free_only=False)
                if b and bd < 0:
                    state.move(uid, b[0], b[1]); improved = True; continue
                if bool(self.units[uid].bundle_key):
                    continue
                old = state.pos[uid]
                order = self.unit_candidate_slots[uid][:]; random.shuffle(order)
                tried = 0
                for d, p in order:
                    if old and (d, p) == old: continue
                    if self.ejection_chain(state, uid, (d, p), max_depth=5):
                        improved = True; break
                    tried += 1
                    if tried >= chain_tries: break
            if not improved:
                break
        return state

    def polish(self, state, max_iter=5000):
        """마무리 하강: 페널티를 엄격히 줄이는 이동/교체만 적용.
        (하드 위반은 가중치가 커서, 총 페널티가 줄면 하드가 늘 수 없음 → 안전)
        """
        it = 0; improved_any = True
        while improved_any and it < max_iter:
            improved_any = False
            targets = [u for u in range(self.n_units)
                       if state.pos[u] is not None and self._unit_badness(state, u) > 0]
            random.shuffle(targets)
            for uid in targets:
                it += 1
                if it >= max_iter: break
                old = state.pos[uid]
                is_bundle = bool(self.units[uid].bundle_key)
                cand = (self._bundle_legal_slots(state, uid) if is_bundle
                        else self.unit_candidate_slots[uid])
                best_slot = None; best_delta = 0
                for d, p in cand:
                    if (d, p) == old: continue
                    delta = state.evaluate_place(uid, d, p)
                    if delta < best_delta:
                        best_delta = delta; best_slot = (d, p)
                if best_slot is not None:
                    state.move(uid, best_slot[0], best_slot[1]); improved_any = True; continue
                if not is_bundle:
                    for u2 in random.sample(self.fixed_uids, min(50, len(self.fixed_uids))):
                        if u2 == uid or state.pos[u2] is None: continue
                        before = state.penalty
                        state.swap(uid, u2)
                        if state.penalty < before:
                            improved_any = True; break
                        state.swap(uid, u2)
        return state

    def solve(self, max_rounds, time_per_round, initial_sol=None, start_round=1,
              progress_cb=None):
        if initial_sol is None: state = self.smart_initial()
        else: state = self._state_from_solution(initial_sol)
        n_units = max(self.n_units, 1)

        best_snap = state.snapshot(); best_pen = state.penalty
        if progress_cb:
            sol = state.get_solution()
            progress_cb(start_round - 1, sol.penalty, sol.penalty, True, "smart_initial (묶음 골격 우선)", sol, "INIT")

        stagn_rounds = 0
        for r in range(start_round, start_round + max_rounds):
            t0 = time.time(); round_improved = False; chunks = 0
            while time.time() - t0 < time_per_round:
                chunks += 1
                n_iter = max(4000, n_units * 8)
                T_s = max(state.penalty * 0.02, 25.0)
                self.local_search(state, n_iter=n_iter, T_start=T_s, T_end=0.5,
                                  reheat_stagnation=max(1200, n_iter // 5),
                                  bundle_move_prob=0.12)
                if state.penalty < best_pen:
                    best_pen = state.penalty; best_snap = state.snapshot()
                    round_improved = True; stagn_rounds = 0
                else:
                    # 개선 없으면 best로 복귀 + 묶음 골격 흔들기(무질서 증가)
                    state.restore(best_snap)
                    self.perturb_bundles(state, random.randint(1, max(1, len(self.bundle_uids) // 2)))
                if best_pen == 0: break
            if not round_improved: stagn_rounds += 1

            # 라운드 단위 큰 정체 → 골격 대폭 재구성
            if stagn_rounds >= 4 and self.bundle_uids:
                state.restore(best_snap)
                self.perturb_bundles(state, max(1, len(self.bundle_uids) * 2 // 3))
                stagn_rounds = 0

            elapsed = int(time.time() - t0)
            note = f"통합탐색 {chunks}청크 + 계층reheat [{elapsed}s]"
            if progress_cb:
                tmp = State(self); tmp.restore(best_snap)
                progress_cb(r, best_pen, state.penalty, round_improved, note, tmp.get_solution(), "UNIFIED")
            if best_pen == 0:
                if progress_cb:
                    tmp = State(self); tmp.restore(best_snap)
                    progress_cb(r, 0, 0, True, "★ 완벽한 해 도달", tmp.get_solution(), "UNIFIED")
                break

        state.restore(best_snap)
        # ── 마무리: 폴리시 + 연쇄 교환 + 묶기 ──
        self.polish(state, max_iter=6000)
        self.chain_repair(state, passes=8)
        self.polish_pairing(state, rounds=5)
        self.chain_repair(state, passes=6)
        self.polish(state, max_iter=2000)
        if state.penalty < best_pen:
            best_pen = state.penalty; best_snap = state.snapshot()
        else:
            state.restore(best_snap)
        if progress_cb:
            sol = state.get_solution()
            progress_cb(start_round + max_rounds, sol.penalty, sol.penalty,
                        True, "폴리시 완료", sol, "POLISH")
        return state.get_solution()


def analyze_h7_floor(data, params):
    """선택한 최대 연속(max_consecutive)에서 교사 연속수업 H7 위반의
    구조적 하한을 계산한다. 점심 분리 없이 1~7교시 연속으로 계산.
    교사의 하루 수업 시수가 무위반 용량을 넘으면 H7=0 불가.
    """
    from itertools import combinations
    from excel_parser import compute_teacher_total_hours
    mc = params.get("max_consecutive", 2)
    periods = list(range(1, 8))

    def max_clean(periods, mc):
        for k in range(len(periods), 0, -1):
            for combo in combinations(periods, k):
                mx = cur = 1
                ok = True
                for i in range(1, len(combo)):
                    if combo[i] == combo[i - 1] + 1:
                        cur += 1; mx = max(mx, cur)
                    else:
                        cur = 1
                if mx <= mc:
                    return k
        return 0

    cap_day = max_clean(periods, mc)       # 교사 하루 무위반 최대 시수
    cap_week = cap_day * 5
    teacher_tot = compute_teacher_total_hours(data)
    # 교사별 주간 시수가 주간 용량을 넘으면, 일부 날은 용량 초과가 불가피
    forced = 0; over = []
    for tch, h in teacher_tot.items():
        if h > cap_week:
            forced += (h - cap_week)
            over.append((tch, h))
    return {
        "max_consecutive": mc,
        "per_day_capacity": cap_day, "weekly_capacity": cap_week,
        "forced_violations": forced, "floor_penalty": forced * PEN_CONSEC,
        "over_teachers": sorted(over, key=lambda x: -x[1]),
        "max_teacher_hours": max(teacher_tot.values()) if teacher_tot else 0,
    }


def analyze_h5_floor(data):
    """(학급,과목) 주당 시수가 5(=요일 수)를 넘으면 어떤 날은 반드시 2회 →
    강제 H5. 그 하한을 계산한다."""
    cs = defaultdict(int)
    for a in data.fixed_assignments:
        cs[(a.class_id, a.subject)] += a.hours
    for bg in data.bundle_groups:
        for m in bg.members:
            cs[(m.class_id, m.subject)] += m.hours
    forced = 0; over = []
    for (cid, subj), h in cs.items():
        if h > 5:
            f = (h - 1) // 5
            forced += f; over.append((cid, subj, h, f))
    return {"forced_violations": forced, "floor_penalty": forced * PEN_SAME_DAY,
            "over": sorted(over, key=lambda x: -x[2])}


def precheck(data, non_class):
    from excel_parser import compute_class_total_hours
    class_hours = compute_class_total_hours(data)
    nc_by_grade = defaultdict(int)
    for s in non_class:
        nc_by_grade[s.grade] += 1
    total_slots = 5 * 7; results = []
    for cid in sorted(class_hours.keys(), key=lambda c: (int(c.split("-")[0]), c.split("-")[1])):
        g = int(cid.split("-")[0]); h = class_hours[cid]
        avail = total_slots - nc_by_grade[g]
        results.append((cid, h, avail, avail - h))
    return results
