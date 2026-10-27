# ============================================================
# Output layout
# ============================================================
# NRV_DVS_ARTIFACT_DIR (프리셋: <sdk>/build/<preset>) 아래 구성별로:
#
#   Debug           개발용. 라이브러리와 테스트 실행 파일을 그대로 둡니다.
#   DebugLeakTest     build/<preset>/<Config>/{libnrv_dvs.so*, internal_*, external_*}
#   RelWithDebInfo  배포용. 빌드가 끝나면 설치 레이아웃(lib/include/share)을 만듭니다.
#   Release           build/<preset>/<Config>/{lib, include, share}  (.cmake/dist.cmake)
#                   빌드 중간 산출물(.so, 테스트)은 빌드 트리 output/<preset>/<Config>/ 에 둡니다.
# ============================================================

set(NRV_DVS_ARTIFACT_DIR "${CMAKE_CURRENT_SOURCE_DIR}/build" CACHE PATH
    "구성별 결과물 루트 (Debug, DebugLeakTest: 개발용 산출물, 그 외: 배포용 설치 레이아웃)")

# 생성기 식을 쓰면 Multi-Config 생성기가 구성 이름을 덧붙이지 않으므로 직접 지정합니다.
set(_nrv_dvs_out_dir
    "$<IF:$<CONFIG:Debug,DebugLeakTest>,${NRV_DVS_ARTIFACT_DIR},${CMAKE_BINARY_DIR}>/$<CONFIG>")
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${_nrv_dvs_out_dir}")
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY "${_nrv_dvs_out_dir}")
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY "${_nrv_dvs_out_dir}")
unset(_nrv_dvs_out_dir)

message(STATUS "nrv_dvs: artifacts -> ${NRV_DVS_ARTIFACT_DIR}/<Config>")
