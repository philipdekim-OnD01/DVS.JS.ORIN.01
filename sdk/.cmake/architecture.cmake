# ============================================================
# Platform / architecture guard
# ============================================================
# 현재 지원: Linux aarch64 (Jetson 위에서 네이티브 빌드).
# 다른 조합은 configure 단계에서 바로 실패시켜, 잘못된 환경에서 애매하게
# 빌드가 진행되는 것을 막습니다.
#
# 출력:
#   NRV_DVS_ARCH_NAME  : "aarch64"
#
# 프리셋(arm64Linux)이 NRV_DVS_TARGET_ARCH=aarch64 를 넘겨주며, 실제
# CMAKE_SYSTEM_PROCESSOR 와 다르면 실패합니다(예: x86_64 PC에서 arm64 프리셋 사용).
#
# CMAKE_SYSTEM_PROCESSOR 예시값: aarch64(Linux GCC), arm64(macOS), x86_64

if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
    message(FATAL_ERROR
        "nrv_dvs: 지원하지 않는 OS '${CMAKE_SYSTEM_NAME}'. 현재 Linux만 지원합니다.")
endif()

string(TOLOWER "${CMAKE_SYSTEM_PROCESSOR}" _nrv_dvs_arch_lc)

if(_nrv_dvs_arch_lc MATCHES "^(aarch64|arm64)$")
    set(NRV_DVS_ARCH_NAME "aarch64")
else()
    message(FATAL_ERROR
        "nrv_dvs: 지원하지 않는 아키텍처 '${CMAKE_SYSTEM_PROCESSOR}'. "
        "현재 Linux aarch64(Jetson 네이티브 빌드)만 지원합니다.")
endif()

if(DEFINED NRV_DVS_TARGET_ARCH AND NOT NRV_DVS_TARGET_ARCH STREQUAL NRV_DVS_ARCH_NAME)
    message(FATAL_ERROR
        "nrv_dvs: 프리셋 아키텍처(${NRV_DVS_TARGET_ARCH})와 "
        "빌드 머신 아키텍처(${NRV_DVS_ARCH_NAME})가 다릅니다.")
endif()

unset(_nrv_dvs_arch_lc)

message(STATUS "nrv_dvs: target = Linux ${NRV_DVS_ARCH_NAME} (raw=${CMAKE_SYSTEM_PROCESSOR})")
