# ============================================================
# 테스트 구성
#  - internal/*.cpp : 내부(white-box) 테스트
#      nrv_dvs_objects 를 직접 링크 → .so 에서 숨겨진 내부 함수와 src/ 비공개 헤더 사용 가능
#      include/pch.h(PCH)도 라이브러리와 똑같이 적용
#  - external/*.cpp : 외부 API 테스트
#      SDK 사용자와 같은 조건: 공유 라이브러리(NrvDvs::nrv_dvs) + include/ 공개 헤더만
#      PCH 없음 → 공개 헤더가 자기 include 만으로 컴파일되는지도 함께 검증
#
#  파일 하나당 실행 파일 하나(internal/foo.cpp → internal_foo), CTest 등록.
#  LABELS 로 구분: ctest --preset <preset> -L internal | -L external
#  프레임워크 의존성 없음: 프로세스가 0으로 종료하면 통과.
#
#  테스트용 파일은 <kind>/resources/ 에 두면, 빌드할 때 테스트 실행 파일 옆
#  resources/ 로 복사됩니다(바뀐 파일만). 테스트는 실행 파일이 있는 디렉터리에서
#  실행하므로(ctest 도 그렇게 등록) 소스에서는 "resources/파일" 로 엽니다.
# ============================================================

# source 디렉터리의 파일들을 테스트 실행 파일 옆 name/ 으로 복사하는 타깃 target
# (빌드할 때마다 바뀐 파일만 복사)
function(nrv_dvs_test_data target source name)
    file(GLOB _files CONFIGURE_DEPENDS "${source}/*")
    set(_dir "${CMAKE_RUNTIME_OUTPUT_DIRECTORY}/${name}")
    add_custom_target(${target} ALL
        COMMAND ${CMAKE_COMMAND} -E make_directory "${_dir}"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different ${_files} "${_dir}"
        VERBATIM)
endfunction()

function(nrv_dvs_add_tests kind)
    set(_resources "${CMAKE_CURRENT_SOURCE_DIR}/${kind}/resources")
    if(IS_DIRECTORY "${_resources}")
        nrv_dvs_test_data(${kind}_resources "${_resources}" resources)
    endif()
    file(GLOB _sources CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/${kind}/*.cpp")
    # zst_to_video 은 libav(FFmpeg 개발 패키지)가 있을 때만 빌드
    if(NOT LIBAV_FOUND)
        list(FILTER _sources EXCLUDE REGEX "/zst_to_video\\.cpp$")
    endif()
    foreach(test_src IN LISTS _sources)
        get_filename_component(_name "${test_src}" NAME_WE)
        set(test_name "${kind}_${_name}")
        add_executable(${test_name} "${test_src}")
        if(kind STREQUAL "internal")
            target_link_libraries(${test_name} PRIVATE nrv_dvs_objects)
            # src/ 헤더가 PCH 에 기대므로 내부 테스트도 같은 PCH 를 씁니다.
            # (REUSE_FROM 은 PIC 등 플래그가 달라 쓸 수 없어 테스트별로 따로 컴파일)
            target_precompile_headers(${test_name} PRIVATE "${PROJECT_SOURCE_DIR}/include/pch.h")
        else()
            target_link_libraries(${test_name} PRIVATE NrvDvs::nrv_dvs)
        endif()
        target_link_libraries(${test_name} PRIVATE nrv_dvs_build_options)
        if(TARGET ${kind}_resources)
            add_dependencies(${test_name} ${kind}_resources)
        endif()
        add_test(NAME ${test_name} COMMAND ${test_name}
                 WORKING_DIRECTORY "$<TARGET_FILE_DIR:${test_name}>")
        set_tests_properties(${test_name} PROPERTIES TIMEOUT 60 LABELS ${kind})
    endforeach()
endfunction()

find_package(PkgConfig REQUIRED)
pkg_check_modules(LIBAV IMPORTED_TARGET libavcodec libavformat libavutil)

nrv_dvs_add_tests(internal)
nrv_dvs_add_tests(external)

# stress_test 는 60초, raw2_2300_test 는 25분, compressor_test 는 20초씩 세 번 수신하므로 기본 제한(60초)보다 여유를 줌
set_tests_properties(external_aer_stress_test PROPERTIES TIMEOUT 1560)
set_tests_properties(external_raw2_stress_test PROPERTIES TIMEOUT 1560)
# aer_crc_test 는 CRC 오류 10개까지 (EvthMax 2496 Mbps 에서 15분에 약 4개 → 1시간 안쪽)
set_tests_properties(external_aer_crc_test PROPERTIES TIMEOUT 14400)
# pix_clk_test 는 group AER 1분
set_tests_properties(external_pix_clk_test PROPERTIES TIMEOUT 90)
set_tests_properties(external_compressor_test PROPERTIES TIMEOUT 120)

# stress_test, raw2_2300_test, compressor_test, record_test, compression_bench, decoder_test 는 settings/ 의 setfile 을 그대로 읽음: 실행 파일 옆
# settings/ 로 복사 (소스에서는 "settings/파일" 로 엶)
nrv_dvs_test_data(test_settings "${PROJECT_SOURCE_DIR}/settings" settings)
add_dependencies(external_aer_stress_test test_settings)
add_dependencies(external_raw2_stress_test test_settings)
add_dependencies(external_aer_crc_test test_settings)
add_dependencies(external_pix_clk_test test_settings)
add_dependencies(external_compressor_test test_settings)
add_dependencies(internal_record_test test_settings)
add_dependencies(internal_compression_bench test_settings)
add_dependencies(internal_decoder_test test_settings)

# compression_bench 는 녹화 4개(약 1.7 GB)를 직접 한 뒤 압축하므로 기본 제한(60초)보다 여유를 줌
set_tests_properties(internal_compression_bench PROPERTIES TIMEOUT 240)
# decoder_test 도 녹화 4개를 한 뒤 해석함
set_tests_properties(internal_decoder_test PROPERTIES TIMEOUT 120)

# 압축 테스트는 결과를 직접 풀어 보므로 zstd 를, 비교 벤치는 zlib 도 함께 링크
find_package(ZLIB REQUIRED)
target_link_libraries(internal_compression_bench PRIVATE zstd::libzstd_shared ZLIB::ZLIB)
target_link_libraries(external_compressor_test PRIVATE zstd::libzstd_shared)

# zst_to_video: 녹화 파일(zstd)을 풀어 FFV1 동영상(mov, 센서 시각)으로 씀. 파일 길이만큼 걸리는
# 유틸리티라 시간 제한을 넉넉히 둠 (RAW2 10초 녹화 = 센서 프레임 약 16,000장)
if(LIBAV_FOUND)
    target_link_libraries(external_zst_to_video PRIVATE zstd::libzstd_shared PkgConfig::LIBAV)
    set_tests_properties(external_zst_to_video PROPERTIES TIMEOUT 3600)
endif()
