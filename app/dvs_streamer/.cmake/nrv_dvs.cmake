# ============================================================
# nrv_dvs SDK
# ============================================================
# 현재 작업 사본의 SDK와 앱을 함께 빌드한다.
# 설치된 구 ABI 라이브러리나 Git main의 이전 디코더를 자동 선택하지 않는다.
# SDK 전체 장치 시험은 앱 빌드에 포함하지 않는다.
# ============================================================
get_filename_component(NRV_DVS_LOCAL_SOURCE_DIR
    "${CMAKE_CURRENT_SOURCE_DIR}/../../sdk" ABSOLUTE)
if(NOT EXISTS "${NRV_DVS_LOCAL_SOURCE_DIR}/CMakeLists.txt")
    message(FATAL_ERROR "같은 저장소의 sdk/ 소스가 필요합니다: ${NRV_DVS_LOCAL_SOURCE_DIR}")
endif()
set(NRV_DVS_BUILD_TESTS OFF CACHE BOOL "앱 빌드에서 장치 시험 제외" FORCE)
set(NRV_DVS_ARTIFACT_DIR "${NRV_DVS_LOCAL_SOURCE_DIR}/build/GCC-arm64"
    CACHE PATH "SDK 구성별 배포 경로")
add_subdirectory("${NRV_DVS_LOCAL_SOURCE_DIR}" "${CMAKE_CURRENT_BINARY_DIR}/sdk")
