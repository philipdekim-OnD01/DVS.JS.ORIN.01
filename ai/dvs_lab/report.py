def grounded_report(summary: dict) -> str:
    if not summary["bins"]:
        return "저장된 이벤트 집계가 없습니다. 행동이나 낙상 여부를 판단할 수 없습니다."
    return (f"이벤트 집계 시간 구간은 {summary['bins']}개입니다. "
            f"ON 이벤트 {summary['on_events']}개, OFF 이벤트 {summary['off_events']}개가 기록되었습니다. "
            "이는 센서 이벤트 수이며 사람 수, 행동 종류 또는 낙상 발생을 의미하지 않습니다. "
            "현재 데이터만으로 실제 시각이나 낙상 여부는 판단할 수 없습니다.")
