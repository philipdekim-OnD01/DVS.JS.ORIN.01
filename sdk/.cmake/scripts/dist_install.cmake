# cmake -P 스크립트: nrv_dvs_dist 타겟(.cmake/dist.cmake)이 호출합니다.
#   NRV_DVS_CONFIG      빌드 구성
#   NRV_DVS_BINARY_DIR  빌드 트리 (cmake_install.cmake 위치)
#   NRV_DVS_DIST_DIR    배포 레이아웃을 만들 디렉터리

if(NRV_DVS_CONFIG MATCHES "^(Debug|DebugLeakTest)$")
    return()
endif()

set(_strip "")
if(NRV_DVS_CONFIG STREQUAL "Release")
    set(_strip "--strip")
endif()

# 지워진 파일이 남지 않도록 매번 새로 만듭니다.
file(REMOVE_RECURSE "${NRV_DVS_DIST_DIR}")
execute_process(
    COMMAND "${CMAKE_COMMAND}" --install "${NRV_DVS_BINARY_DIR}"
            --config "${NRV_DVS_CONFIG}" --prefix "${NRV_DVS_DIST_DIR}" ${_strip}
    OUTPUT_QUIET
    COMMAND_ERROR_IS_FATAL ANY
)
