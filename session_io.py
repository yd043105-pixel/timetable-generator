# -*- coding: utf-8 -*-
"""이어서 돌리기 세션(.json) 저장/로드.

시수표 데이터·입력조건·현재 시간표 배치를 한 파일에 담아, 나중에 다시 열어
그 시간표에서 이어서 최적화할 수 있게 한다(시수표 파일 없이도 복원 가능).
"""
import json
from dataclasses import asdict

from models import (SchoolData, SpecialRoom, FixedAssignment, BundleGroup,
                    BundleMember, NonClassSlot, TeacherUnavailable,
                    SimilarSubjectGroup, safe_init)

FORMAT = "timetable-session-1"


def _data_from_dict(d):
    sd = SchoolData(
        year=d.get("year", 2026), semester=d.get("semester", 1),
        grades=d.get("grades", [1, 2, 3]),
        classes_per_grade={int(k): v for k, v in d.get("classes_per_grade", {}).items()},
        teachers=d.get("teachers", []), subjects=d.get("subjects", []),
    )
    sd.special_rooms = [safe_init(SpecialRoom, x) for x in d.get("special_rooms", [])]
    sd.fixed_assignments = [safe_init(FixedAssignment, x) for x in d.get("fixed_assignments", [])]
    bg = []
    for x in d.get("bundle_groups", []):
        g = safe_init(BundleGroup, x)
        g.members = [safe_init(BundleMember, mm) for mm in x.get("members", [])]
        bg.append(g)
    sd.bundle_groups = bg
    return sd


def save_session(path, data, non_class, unavail, similar, params,
                 year, semester, warm_units):
    obj = {
        "format": FORMAT,
        "year": year,
        "semester": semester,
        "params": params,
        "data": asdict(data),
        "non_class": [asdict(x) for x in non_class],
        "unavail": [asdict(x) for x in unavail],
        "similar": [asdict(x) for x in similar],
        "warm_units": [list(dp) if dp else None for dp in warm_units],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def load_session(path):
    with open(path, "r", encoding="utf-8") as f:
        obj = json.load(f)
    if obj.get("format") != FORMAT:
        raise ValueError("이어서 돌리기용 파일이 아닙니다.")
    data = _data_from_dict(obj["data"])
    non_class = [safe_init(NonClassSlot, x) for x in obj.get("non_class", [])]
    unavail = [safe_init(TeacherUnavailable, x) for x in obj.get("unavail", [])]
    similar = [safe_init(SimilarSubjectGroup, x) for x in obj.get("similar", [])]
    warm_units = [tuple(dp) if dp else None for dp in obj.get("warm_units", [])]
    return {
        "year": obj.get("year", 2026),
        "semester": obj.get("semester", 1),
        "params": obj.get("params", {}),
        "data": data,
        "non_class": non_class,
        "unavail": unavail,
        "similar": similar,
        "warm_units": warm_units,
    }
