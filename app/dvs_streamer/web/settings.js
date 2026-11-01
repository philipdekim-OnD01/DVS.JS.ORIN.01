// Menu groups and register presets adapted from DVS.Decoder.RP5.01
// @01e1345db029a633cd97108db82c3629b0b36491, hdmi_controls.py / server_production.py.
// Sensor reads use the Orin viewer API; display options affect this browser only.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const dialog = $("settings-dialog");
  const presets = {
    mipi: [0x3901, 0x3906, 0x3907, 0x3908, 0x3915],
    sensitivity: [0x0157, 0x0166, 0x0167, 0x0168],
    event: [0x392c, 0x3282],
    roi: Array.from({ length: 13 }, (_, i) => 0x3300 + i),
  };
  const hex = (value, width = 2) => "0x" + Number(value).toString(16).toUpperCase().padStart(width, "0");
  let info = null, loading = false, reading = false, lastReadAt = 0;
  const message = (id, text, kind = "") => {
    $(id).textContent = text;
    $(id).className = "settings-message" + (kind ? " " + kind : "");
  };
  function cell(row, text, className = "") {
    const td = document.createElement("td");
    td.textContent = text;
    td.className = className;
    row.append(td);
  }
  function description(target, values) {
    const list = $(target);
    list.replaceChildren();
    for (const [key, value] of values) {
      const div = document.createElement("div"), dt = document.createElement("dt"), dd = document.createElement("dd");
      dt.textContent = key;
      dd.textContent = value ?? "—";
      div.append(dt, dd);
      list.append(div);
    }
  }
  async function request(url, options) {
    const response = await fetch(url, { cache: "no-store", ...options });
    let result;
    try { result = await response.json(); }
    catch (_) { throw new Error(`서버 응답을 읽지 못했습니다 (HTTP ${response.status}).`); }
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    return result;
  }
  function renderSetfile() {
    const query = $("setfile-filter").value.trim().toLowerCase();
    const body = $("setfile-results");
    body.replaceChildren();
    const entries = (info?.sensor?.entries || []).filter((entry) =>
      [entry.address_hex, entry.value_hex, entry.comment, String(entry.line)].join(" ").toLowerCase().includes(query));
    for (const entry of entries) {
      const row = document.createElement("tr");
      cell(row, entry.line);
      cell(row, entry.address_hex);
      cell(row, entry.value_hex);
      cell(row, [entry.comment, entry.applied === false ? "수신기가 별도 제어 · 목록 적용 제외" : ""].filter(Boolean).join(" · "));
      body.append(row);
    }
    if (!entries.length) {
      const row = document.createElement("tr");
      cell(row, "검색 결과가 없습니다.", "settings-empty");
      row.firstChild.colSpan = 4;
      body.append(row);
    }
  }
  function renderInfo() {
    const sensor = info.sensor || {}, capture = info.capture || {}, system = info.system || {};
    const device = sensor.device || {}, file = sensor.file || {}, stream = capture.stream || {};
    description("settings-device", [["장치", device.path], ["I²C 주소", device.address_hex],
      ["드라이버", device.driver], ["읽기 준비", device.available ? "사용 가능" : "접근 불가"]]);
    $("setfile-name").textContent = file.name || "설정 파일 정보 없음";
    $("setfile-hash").textContent = `${file.path || "—"}\nSHA-256: ${file.sha256 || "—"}`;
    $("setfile-summary").textContent = `수신 시작 시 사용한 설정 목록 · ${sensor.operation_count ?? 0}개 항목 · 파일 순서대로 표시합니다.`;
    $("setfile-note").textContent = sensor.note || "설정 파일 값은 센서에서 읽은 현재값과 다를 수 있습니다.";
    $("settings-format").textContent = `${(capture.format || sensor.format || "—").toUpperCase()} · 960 × 720`;
    const number = (v) => v == null ? "—" : Number(v).toLocaleString();
    description("settings-health", [
      ["수신 상태", `${capture.state || "—"} · 표본 나이 ${capture.update_age == null ? "—" : Number(capture.update_age).toFixed(2)} s`],
      ["호스트 / 아키텍처", `${system.hostname || "—"} / ${system.architecture || "—"}`],
      ["커널", system.kernel], ["형식 / 세션 PID", `${capture.format || "—"} / ${capture.session_pid || "—"}`],
      ["실행 시간", `${number(capture.seconds)} s`], ["수신 버퍼", number(stream.buffers)],
      ["수신 오류 버퍼", number(stream.error_buffers)], ["수신 순번 누락", number(stream.dropped)],
      ["드라이버 버퍼 고갈", number(stream.starved)], ["디코더 형식 오류", number((capture.invalid || 0) + (capture.unknown || 0))],
      ["RAW2 시각 모호성", number(capture.raw2_timestamp_ambiguous)], ["RAW2 후보 역행 / 동일", number(capture.raw2_timestamp_order_errors)],
      ["원본 파이프 누락", number(capture.raw_pipe_dropped)], ["화면 파이프 생략", number(capture.screen_pipe_dropped)],
      ["녹화 상태", capture.dump?.state], ["센서 I²C", device.bound ? "nrv_dvs 연결됨" : "드라이버 연결 안 됨"],
    ]);
    $("health-time").textContent = `조회 시각 ${new Date().toLocaleTimeString()} · 새로고침 시 갱신됩니다.`;
    renderSetfile();
  }
  async function refresh() {
    if (loading) return;
    loading = true;
    $("settings-refresh").disabled = true;
    $("register-read").disabled = true;
    message("settings-message", "설정과 수신 상태를 불러오는 중입니다.");
    try {
      info = await request("/api/settings");
      renderInfo();
      message("settings-message", info.sensor?.error || "현재 수신기의 설정과 장치 정보를 불러왔습니다.", info.sensor?.error ? "bad" : "");
    } catch (error) {
      info = null;
      message("settings-message", `연결 오류: ${error.message}`, "bad");
    } finally {
      loading = false;
      $("settings-refresh").disabled = false;
      $("register-read").disabled = reading || !info?.token;
    }
  }
  function tab(name, focus = false) {
    for (const button of dialog.querySelectorAll("[role=tab]")) {
      const selected = button.dataset.panel === name;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
      $("panel-" + button.dataset.panel).hidden = !selected;
      if (selected && focus) button.focus();
    }
    if (name === "display") syncDisplay();
  }
  const tabs = [...dialog.querySelectorAll("[role=tab]")];
  tabs.forEach((button, index) => {
    button.addEventListener("click", () => tab(button.dataset.panel));
    button.addEventListener("keydown", (event) => {
      let next;
      if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
      else if (event.key === "ArrowLeft") next = (index + tabs.length - 1) % tabs.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = tabs.length - 1;
      if (next !== undefined) { event.preventDefault(); tab(tabs[next].dataset.panel, true); }
    });
  });
  $("settings-open").addEventListener("click", () => { dialog.showModal(); syncDisplay(); refresh(); });
  $("settings-close").addEventListener("click", () => dialog.close());
  $("settings-refresh").addEventListener("click", refresh);
  $("setfile-filter").addEventListener("input", renderSetfile);
  dialog.querySelectorAll("[data-registers]").forEach((button) => button.addEventListener("click", () => {
    $("register-addresses").value = presets[button.dataset.registers].map((v) => hex(v, 4)).join(", ");
    message("register-message", "주소 모음을 선택했습니다. 읽기를 눌러 현재값을 확인하세요.");
  }));
  $("register-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (reading || loading) return;
    message("register-message", "");
    try {
      if (!info?.token) throw new Error("설정창을 다시 열어 서버 연결을 확인해 주세요.");
      const parts = $("register-addresses").value.trim().split(/[\s,]+/);
      if (!parts.length || parts.length > 16 || parts.some((part) => !/^(?:0x)?[0-9a-f]{1,4}$/i.test(part)))
        throw new Error("0000~FFFF 범위의 16진수 주소를 1~16개 입력해 주세요.");
      if (Date.now() - lastReadAt < 1000) throw new Error("레지스터 읽기는 1초 간격으로 요청해 주세요.");
      reading = true;
      $("register-read").disabled = true;
      $("register-read").textContent = "읽는 중…";
      message("register-message", "센서에서 현재 값을 읽고 있습니다.");
      lastReadAt = Date.now();
      const result = await request("/api/registers/read", {
        method: "POST", headers: { "Content-Type": "application/json", "X-DVS-Token": info.token },
        body: JSON.stringify({ addresses: parts.map((part) => parseInt(part.replace(/^0x/i, ""), 16)) }),
      });
      const body = $("register-results");
      body.replaceChildren();
      for (const value of result.registers) {
        const row = document.createElement("tr");
        cell(row, value.address_hex);
        cell(row, value.value_hex);
        cell(row, value.setfile_value == null ? "—" : hex(value.setfile_value));
        cell(row, value.matches_setfile == null ? "비교값 없음" : value.matches_setfile ? "일치" : "다름",
          value.matches_setfile == null ? "" : value.matches_setfile ? "settings-match" : "settings-different");
        body.append(row);
      }
      const group = result.registers.find((value) => value.address === 0x392c);
      const bits = $("register-bits");
      bits.hidden = !group;
      bits.replaceChildren();
      if (group) {
        const title = document.createElement("strong");
        title.textContent = "0x392C 비트 상태 · 센서에서 읽은 값";
        bits.append(title, document.createElement("br"));
        for (const [name, bit] of [["CID", 7], ["Flip Row", 6], ["Mirror Column", 5], ["Merged Group", 3], ["All Event", 2], ["ON Only", 1], ["OFF Only", 0]]) {
          const badge = document.createElement("span"), enabled = Boolean(group.value & (1 << bit));
          badge.textContent = `${name}: ${enabled ? "1" : "0"}`;
          badge.className = enabled ? "enabled" : "";
          bits.append(badge);
        }
      }
      message("register-message", `${result.registers.length}개 읽기 완료 · ${new Date(result.read_at).toLocaleTimeString()} · ${result.device}`, "success");
    } catch (error) {
      message("register-message", `읽기 실패: ${error.message} (표의 기존 값은 이전 조회 결과입니다.)`, "bad");
    } finally {
      reading = false;
      $("register-read").disabled = loading || !info?.token;
      $("register-read").textContent = "레지스터 읽기";
    }
  });

  function syncDisplay() {
    const options = window.dvsDisplay.get();
    $("display-preview").value = $("preview").value;
    $("display-polarity").value = String(options.polarity);
    $("display-denoise").value = String(options.denoise);
    $("display-flip-x").checked = options.flipX;
    $("display-flip-y").checked = options.flipY;
    $("display-pause").textContent = options.paused ? "화면 다시 보기" : "화면 일시정지";
    ["roi-x0", "roi-x1", "roi-y0", "roi-y1"].forEach((id, i) => { $(id).value = options.roi[i]; });
  }
  function updateDisplay(options) { window.dvsDisplay.set(options); syncDisplay(); }
  $("display-preview").addEventListener("change", () => {
    $("preview").value = $("display-preview").value;
    $("preview").dispatchEvent(new Event("change", { bubbles: true }));
    message("display-message", "프리뷰 변경을 요청했습니다. 적용값은 메인 화면에서 확인할 수 있습니다.");
  });
  $("display-polarity").addEventListener("change", () => updateDisplay({ polarity: Number($("display-polarity").value) }));
  $("display-denoise").addEventListener("change", () => updateDisplay({ denoise: Number($("display-denoise").value) }));
  $("display-flip-x").addEventListener("change", () => updateDisplay({ flipX: $("display-flip-x").checked }));
  $("display-flip-y").addEventListener("change", () => updateDisplay({ flipY: $("display-flip-y").checked }));
  $("display-pause").addEventListener("click", () => updateDisplay({ paused: !window.dvsDisplay.get().paused }));
  $("display-roi-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const roi = ["roi-x0", "roi-x1", "roi-y0", "roi-y1"].map((id) => Number($(id).value));
    if (roi.some((v) => !Number.isInteger(v)) || roi[0] < 0 || roi[1] > 959 || roi[2] < 0 || roi[3] > 719 || roi[0] > roi[1] || roi[2] > roi[3]) {
      message("display-message", "ROI 시작값은 끝값 이하여야 합니다. X는 0~959, Y는 0~719 범위입니다.", "bad");
      return;
    }
    updateDisplay({ roi });
    message("display-message", `표시 ROI: X ${roi[0]}–${roi[1]}, Y ${roi[2]}–${roi[3]}`, "success");
  });
  $("display-roi-reset").addEventListener("click", () => {
    updateDisplay({ roi: [0, 959, 0, 719] });
    message("display-message", "전체 센서 영역을 표시합니다.");
  });
  $("display-reset").addEventListener("click", () => {
    updateDisplay({ polarity: 0, denoise: 1, flipX: false, flipY: false, paused: false, roi: [0, 959, 0, 719] });
    message("display-message", "브라우저 표시 설정을 초기화했습니다. 프리뷰 간격은 유지됩니다.");
  });
  $("display-save").addEventListener("click", () => {
    const view = $("view");
    view.toBlob((blob) => {
      if (!blob) { message("display-message", "PNG를 만들지 못했습니다.", "bad"); return; }
      const url = URL.createObjectURL(blob), link = document.createElement("a");
      link.href = url;
      link.download = `dvs-${new Date().toISOString().replace(/[:.]/g, "-")}.png`;
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      message("display-message", "현재 표시 화면의 PNG 다운로드를 시작했습니다.", "success");
    }, "image/png");
  });
})();
