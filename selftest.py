# -*- coding: utf-8 -*-
"""자가진단: ortools/솔버가 어디서 죽는지 단계별로 파일에 기록한다.
이 스크립트(또는 빌드한 exe)를 실행하면 같은 폴더에 selftest_result.txt 가 생긴다.
"""
import os
import sys
import datetime

LOG = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "selftest_result.txt")


def w(msg):
    line = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    open(LOG, "w", encoding="utf-8").close()
    w("=== 시간표 생성기 자가진단 시작 ===")
    w(f"파이썬 버전: {sys.version}")
    w(f"플랫폼: {sys.platform}")

    # 1) ortools import
    try:
        w("1) ortools 불러오기 시도...")
        from ortools.sat.python import cp_model
        import ortools
        w(f"   성공! ortools 버전: {getattr(ortools, '__version__', '?')}")
    except Exception as e:
        w(f"   [실패] {type(e).__name__}: {e}")
        w(">>> ortools 자체를 불러오지 못합니다. 이게 원인입니다.")
        return

    # 2) 아주 작은 CP-SAT 모델 풀기 (CPU 호환성 핵심 테스트)
    try:
        w("2) 초간단 CP-SAT 풀이 시도 (x+y<=3 최대화)...")
        m = cp_model.CpModel()
        x = m.NewIntVar(0, 10, "x"); y = m.NewIntVar(0, 10, "y")
        m.Add(x + y <= 3); m.Maximize(x + y)
        s = cp_model.CpSolver()
        s.parameters.max_time_in_seconds = 5
        st = s.Solve(m)
        w(f"   성공! 상태={s.StatusName(st)}, x={s.Value(x)}, y={s.Value(y)}")
    except Exception as e:
        w(f"   [실패] {type(e).__name__}: {e}")
        w(">>> CP-SAT 풀이에서 죽습니다. CPU/환경 호환성 문제일 수 있습니다.")
        return

    # 3) 멀티스레드(8 workers) 테스트 — 여기서 죽는 경우가 많음
    try:
        w("3) 멀티스레드(8 workers) CP-SAT 시도...")
        m = cp_model.CpModel()
        xs = [m.NewBoolVar(f"b{i}") for i in range(20)]
        m.Add(sum(xs) == 10)
        s = cp_model.CpSolver()
        s.parameters.max_time_in_seconds = 5
        s.parameters.num_search_workers = 8
        st = s.Solve(m)
        w(f"   성공! 상태={s.StatusName(st)}")
    except Exception as e:
        w(f"   [실패] {type(e).__name__}: {e}")
        w(">>> 멀티스레드에서 죽습니다. workers=1 로 바꾸면 해결될 수 있습니다.")
        return

    # 4) solution callback 테스트 — 우리 진행상황 표시 방식
    try:
        w("4) solution callback 테스트...")
        class CB(cp_model.CpSolverSolutionCallback):
            def __init__(self): super().__init__(); self.c = 0
            def on_solution_callback(self):
                self.c += 1
        m = cp_model.CpModel()
        xs = [m.NewBoolVar(f"c{i}") for i in range(15)]
        m.Add(sum(xs) >= 5); m.Maximize(sum(xs))
        s = cp_model.CpSolver()
        s.parameters.max_time_in_seconds = 5
        s.parameters.num_search_workers = 8
        cb = CB()
        st = s.Solve(m, cb)
        w(f"   성공! 콜백 호출 {cb.c}회, 상태={s.StatusName(st)}")
    except Exception as e:
        w(f"   [실패] {type(e).__name__}: {e}")
        w(">>> 진행상황 콜백에서 죽습니다.")
        return

    w("=== 모든 단계 통과! ortools는 이 PC에서 정상 작동합니다. ===")
    w("이 경우 문제는 ortools가 아니라 다른 곳(시수표 파싱 등)일 수 있습니다.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        w(f"[예상치 못한 오류] {type(e).__name__}: {e}")
        import traceback
        w(traceback.format_exc())
    print("\n진단이 끝났습니다. 같은 폴더의 selftest_result.txt 를 확인하세요.")
    try:
        input("엔터를 누르면 닫힙니다...")
    except Exception:
        pass
