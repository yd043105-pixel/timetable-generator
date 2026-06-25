"""학교 시간표 생성기 v3.4.0 - 데이터 모델"""
from dataclasses import dataclass, field, fields
from typing import List, Dict, Tuple, Optional


def safe_init(cls, d: dict):
    """모델이 가진 필드만 골라서 안전하게 인스턴스 생성 (구버전/신버전 JSON 호환)"""
    valid = {f.name for f in fields(cls)}
    filtered = {k: v for k, v in d.items() if k in valid}
    return cls(**filtered)


@dataclass
class FixedAssignment:
    """학교지정과목 (단일 학급에 배정)"""
    teacher: str
    subject: str
    class_id: str
    hours: int


@dataclass
class BundleMember:
    """묶음 멤버 (한 묶음 슬롯에서 한 학급을 담당)"""
    teacher: str
    subject: str
    class_id: str
    hours: int = 1  # 같은 학급의 분담 시간 (보통 1, 분담시 누적)


@dataclass
class BundleGroup:
    """묶음수업 그룹 (예: 2학년 A 묶음, 슬롯 3시간)"""
    grade: int
    code: str  # A, B, C ...
    hours: int  # 묶음 슬롯 수
    members: List[BundleMember] = field(default_factory=list)


@dataclass
class NonClassSlot:
    """비수업 슬롯 (자율/창체/동아리 등)"""
    grade: int
    day: str
    period: int
    label: str = "자율"
    memo: str = ""


@dataclass
class TeacherUnavailable:
    """교사 불가 시간"""
    teacher: str
    day: str
    period: int
    reason: str = ""


@dataclass
class SimilarSubjectGroup:
    """유사과목 그룹 (같은 날 회피)"""
    name: str
    subjects: List[str] = field(default_factory=list)


@dataclass
class SpecialRoom:
    """특별실 (학년 헤더 아래의 비숫자 라벨 컬럼)"""
    grade: int
    code: str  # 도, 음, 미 ...
    full_name: str = ""


@dataclass
class SchoolData:
    year: int = 2026
    semester: int = 1
    grades: List[int] = field(default_factory=lambda: [1, 2, 3])
    classes_per_grade: Dict[int, List[str]] = field(default_factory=dict)
    special_rooms: List[SpecialRoom] = field(default_factory=list)
    fixed_assignments: List[FixedAssignment] = field(default_factory=list)
    bundle_groups: List[BundleGroup] = field(default_factory=list)
    teachers: List[str] = field(default_factory=list)
    subjects: List[str] = field(default_factory=list)
