/*
 * nrv_dvs 레지스터 쓰기 opcode (Device::SetRegisterList 의 원소)
 *
 *   31      24 23                8 7           0
 *  +----------+-------------------+------------+
 *  | 0 (예약) |   주소 (16비트)   | 값 (8비트) |
 *  +----------+-------------------+------------+
 *
 * 목록은 순서대로 STREAMON 때 센서에 쓰입니다(정렬·중복 제거 없음).
 * 레지스터 0x0100(스트림 on/off)은 드라이버 전용이라 넣을 수 없습니다.
 * C/C++ 공용 헤더입니다(의존 헤더 없음). 인자는 리터럴이나 부호 없는 타입을
 * 쓰세요(부호 있는 int 변수는 -Wsign-conversion 경고).
 */
#ifndef NRV_DVS_OPCODE_H_
#define NRV_DVS_OPCODE_H_

/* 주소/값 -> opcode.  예) NRV_DVS_OP(0x3901, 0x31) == 0x00390131 */
#define NRV_DVS_OP(addr, value) ((((addr) & 0xFFFFu) << 8) | ((value) & 0xFFu))

/* opcode -> 주소 / 값 */
#define NRV_DVS_OP_ADDR(op) (((op) >> 8) & 0xFFFFu)
#define NRV_DVS_OP_VALUE(op) ((op) & 0xFFu)

#endif /* NRV_DVS_OPCODE_H_ */
