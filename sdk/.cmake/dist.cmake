# ============================================================
# Distribution layout (RelWithDebInfo / Release)
# ============================================================
# 입력:
#   PROJECT_NAME, NRV_DVS_ARTIFACT_DIR (output_layout.cmake)
# 산출:
#   nrv_dvs_dist (ALL 타겟) — RelWithDebInfo/Release 빌드 끝에
#   ${NRV_DVS_ARTIFACT_DIR}/<Config>/ 를 지우고 install 규칙(install.cmake)대로
#   다시 채웁니다. Debug, DebugLeakTest 에서는 아무것도 하지 않습니다.
#     RelWithDebInfo : 디버그 심볼 유지
#     Release        : 설치 시 strip
# ============================================================

add_custom_target(nrv_dvs_dist ALL
    COMMAND ${CMAKE_COMMAND}
            -DNRV_DVS_CONFIG=$<CONFIG>
            -DNRV_DVS_BINARY_DIR=${CMAKE_BINARY_DIR}
            -DNRV_DVS_DIST_DIR=${NRV_DVS_ARTIFACT_DIR}/$<CONFIG>
            -P "${CMAKE_CURRENT_LIST_DIR}/scripts/dist_install.cmake"
    COMMENT "nrv_dvs: distribution layout ($<CONFIG>)"
    VERBATIM
)
# 설치 대상이 모두 만들어진 뒤에 실행
add_dependencies(nrv_dvs_dist ${PROJECT_NAME})

# ============================================================
# Release: .comment 섹션 제거
# ============================================================
# -fno-ident 로 우리 오브젝트에는 컴파일러 버전 문자열이 없지만, 링크에 함께 들어가는
# 툴체인 시작 파일(crti.o, crtbeginS.o 등)이 .comment("GCC: ...")를 가져옵니다.
# 링크 후 섹션째 지웁니다. (strip/심볼 제거는 compiler_options.cmake 의 Release 링크 옵션)
add_custom_command(TARGET ${PROJECT_NAME} POST_BUILD
    COMMAND "$<$<CONFIG:Release>:${CMAKE_OBJCOPY};--remove-section=.comment;$<TARGET_FILE:${PROJECT_NAME}>>"
    COMMAND_EXPAND_LISTS
    VERBATIM
)
