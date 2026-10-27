cmake_minimum_required(VERSION 3.25)
# ============================================================
# dvs_streamer: nrv_dvs SDK 를 쓰는 수신 앱 (Jetson)
# ============================================================
#   cmake --preset GCC-arm64                          # configure (Ninja Multi-Config)
#   cmake --build --preset RelWithDebInfo-GCC-arm64   # 또는 Debug- / DebugLeakTest- / Release-
#
# SDK: 같은 저장소 sdk/를 함께 빌드합니다(.cmake/nrv_dvs.cmake).
# 설치된 라이브러리나 Git main으로 대체하지 않으며 SDK 장치 시험은 빌드하지 않습니다.
#
# 소스는 자동 수집됩니다: src/ 아래 .cpp 를 추가하고 다시 빌드하면 됩니다(CONFIGURE_DEPENDS).
# python/ 아래 .py(실행 스크립트 run.py, 웹 서버 viewer.py), web/(웹 화면 html/css/js),
# settings/(센서 setfile), bedside/(bedside 화면과 어댑터)는
# 빌드할 때마다 실행 파일 옆으로 복사됩니다. 실행: build/<preset>/<Config>/run.py start
#   실행에 필요한 패키지: python3-numpy python3-yaml python3-pil
# ============================================================

project(dvs_streamer VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD             20)
set(CMAKE_CXX_STANDARD_REQUIRED    ON)
set(CMAKE_CXX_EXTENSIONS          OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS  ON)

include("${CMAKE_CURRENT_SOURCE_DIR}/.cmake/compiler_options.cmake")
include("${CMAKE_CURRENT_SOURCE_DIR}/.cmake/nrv_dvs.cmake")

# ============================================================
# Output: build/<preset>/<Config>/dvs_streamer
# ============================================================
set(DVS_STREAMER_ARTIFACT_DIR "${CMAKE_CURRENT_SOURCE_DIR}/build" CACHE PATH "구성별 결과물 루트")
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${DVS_STREAMER_ARTIFACT_DIR}/$<CONFIG>")

# ============================================================
# Target – dvs_streamer
# ============================================================
file(GLOB_RECURSE DVS_STREAMER_SOURCES CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/src/*.cpp")
add_executable(${PROJECT_NAME} ${DVS_STREAMER_SOURCES})
target_link_libraries(${PROJECT_NAME} PRIVATE dvs_streamer_build_options NrvDvs::nrv_dvs)

# ============================================================
# Python 스크립트·웹 화면·setfile: 컴파일 없이 build/<preset>/<Config>/ 로 복사
# ============================================================
file(GLOB DVS_STREAMER_PYTHON CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/python/*.py")
add_custom_target(${PROJECT_NAME}_runtime ALL
    COMMAND ${CMAKE_COMMAND} -E copy_if_different ${DVS_STREAMER_PYTHON} $<TARGET_FILE_DIR:${PROJECT_NAME}>
    COMMAND ${CMAKE_COMMAND} -E copy_directory "${CMAKE_CURRENT_SOURCE_DIR}/web"
            $<TARGET_FILE_DIR:${PROJECT_NAME}>/web
    COMMAND ${CMAKE_COMMAND} -E copy_directory "${CMAKE_CURRENT_SOURCE_DIR}/settings"
            $<TARGET_FILE_DIR:${PROJECT_NAME}>/settings
    COMMAND ${CMAKE_COMMAND} -E copy_directory "${CMAKE_CURRENT_SOURCE_DIR}/bedside"
            $<TARGET_FILE_DIR:${PROJECT_NAME}>/bedside
    VERBATIM
)

# ============================================================
# compile_commands.json copy (clangd: .clangd 의 CompilationDatabase: output)
# ============================================================
add_custom_target(copy_compile_commands ALL
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
            ${CMAKE_BINARY_DIR}/compile_commands.json
            ${CMAKE_BINARY_DIR}/../compile_commands.json
    VERBATIM
)
