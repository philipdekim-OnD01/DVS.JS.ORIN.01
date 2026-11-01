# Open issues

## Deployment evidence update — 2026-11-01

The historical issues below describe the original review state. A subsequent
Orin Nano Super R39.2.1 deployment verified module/overlay activation after a
power cycle, stock NVIDIA camera module use, `/dev/video0`, register-list
application and RAW2 live decoding/display. See the [deployment record](deployment/JETSON-2026-11-01.md)
and [installation guides](guides/README.md).
This does not close the unmeasured lane-rate/deskew/clock questions, all control
edge cases, GROUP AER runtime testing, or long-duration reliability testing.
RAW2 timestamp ambiguity/order counters also remain documented limitations.

## 1. NVCSI deskew not used (recorded 2026-10-12, review required)

**Situation**
- Sensor setfile `DPHY_PLL_PMS` 30A5/30A6/30A7 = 00/8D/00 -> intended lane rate
  2.5 Gbps/lane (**not measured**). The driver no longer contains the setfile;
  `app/dvs_live` sends the setfile unchanged through `nrv_register_writes`
  (only a different setfile can change these registers).
- Receiver side: fixed in the DT mode0 node, `pix_clk_hz = "1250000000"`, 4 lanes,
  8 bit -> `pixel_clock` 1.25e9 pixel/s, `mipi_clock` 1.25 GHz. The NVCSI
  `mipi_clock` equals what the removed runtime receiver-rate module parameter
  set; the VI bind-time pixel rate does not (issue 3).
- NVIDIA stock `nvidia-oot/drivers/media/platform/tegra/camera/csi/csi.c`:
  - comment: "deskew should be run when the sensor data rate is >= 1.5 gbps"
  - `CLK_HZ_FOR_DESKEW = 1500 MHz / 2`; `deskew_setup()` runs only when
    `pix_clk_hz >= CLK_HZ_FOR_DESKEW && sig_props->deskew_initial_enable`.
- The CAM1 overlay mode node has no `deskew_initial_enable` (nor
  `deskew_periodic_enable`) -> **deskew is off**. This tree does not change that.

**Risk**
- Error-free reception at 2.5 Gbps without deskew may be due to the current DVK
  board and short FPC cabling.
- Other cable lengths, carrier boards or temperatures may cause CRC/ECC errors and
  dropped frames.

**To check**
1. Whether the sensor can emit D-PHY deskew sync patterns (initial/periodic):
   datasheet and registers.
2. Measure the real lane rate (scope on the clock lane, or verify the PMS formula).
3. If supported, add `deskew_initial_enable = "true";` to the DT mode node and
   compare error rates (including longer cables).
4. If not supported, document supported cable length / board conditions in the
   distribution documentation.

**Status:** not reviewed. Test item before distribution.

## 2. Boot activation unverified

`boot/extlinux-entry.example` is a template. UEFI application of `OVERLAYS` to the
firmware-selected base DTB, module signing and the runtime receive path are
unverified. Requires a separately approved boot + receive test.
That test must also confirm that the stock `tegra-camera.ko` is sufficient: the
`tegra_camera` behaviors of the old bring-up module (e.g. `nrv_cam1_lp_bypass=Y`)
and the VI data type patch (issue 7) are intentionally absent from this tree. The 2.5 Gbps lane rate (issue 1) is also
unmeasured.
The `nrv_register_writes` V4L2 control (replacing all module parameters) is also
unverified at runtime: visibility on `/dev/video0` after VI bind, default (empty
marker) initialisation, `EBUSY` while streaming and the write order on the bus.
The new driver must be booted together with the new `cam1.dtbo`
(`pix_clk_hz` 1250000000); with the old DT (500000000) the receiver would be
programmed for `mipi_clock` 500 MHz instead of 1.25 GHz.
See also issue 3 (VI bind-time pixel rate).
**Current state: NOT loaded, NOT booted, NOT receive-tested.**

## 3. VI / camera-platform pixel rate now 1.25e9 from bind (recorded 2026-10-12)

**Situation**
- The receiver rate moved from a runtime module parameter to the DT
  (`pix_clk_hz = "1250000000"` in cam1.dts mode0, previously `"500000000"`).
- Bind time (VI, once): `tegra_channel_init_subdevices()` ->
  `tegra_channel_populate_dev_info()` -> `tegra_channel_get_sensor_peak_vals()`
  (vi/channel.c) copies the mode `pixel_clock` into `cdev->pixel_rate` / `bw`,
  registered via `tegra_camera_device_register()`.
- Stream on (runtime): `tegra_camera_update_clknbw()` ->
  `calculate_and_set_device_clock()` (tegra_camera_platform.c) requests the
  NVCSI clock, the VI clock and the ISO bandwidth from that bind-time rate.
  NVCSI CIL programming (`mipi_clock`, settle, polarity) is read from the mode
  at stream on instead.
- Baseline: in the observed module reload the journal shows
  `subdev nrv_dvs 2-0010 bound` before `DVS driver loaded`, i.e. before the old
  driver applied its receiver-rate parameter. The bind-time path therefore saw
  **500e6**, while NVCSI CIL got 1.25 GHz at stream on. (At boot the order may
  differ; not checked.)
- New tree: the bind-time path sees **1.25e9**, deterministically. NVCSI CIL
  values are unchanged. It cannot be avoided without changing the receiver
  design: `pix_clk_hz` (and `serdes_pix_clk_hz`, if used) feeds both
  `mipi_clock` and the VI rate. The design is intentionally left as is.
- The VI `SENSOR_SIGNAL_PROPERTIES` control also reports 1.25e9 (informational).

**Risk**
- About 2.5x higher requested NVCSI/VI clocks and ISO (EMC) bandwidth than in
  the validated baseline. Probably harmless or beneficial (requests are clamped
  to the supported clock rates, and the real link is 2.5 Gbps x 4 lanes), but
  the validated receive test ran with the lower requests. Possible effects:
  higher power/EMC load, or a bandwidth/clock request that fails or is clamped
  differently.

**To check (during the approved boot + receive test)**
1. At stream on, read the NVCSI, VI and EMC clock rates (e.g.
   `sudo cat /sys/kernel/debug/bpmp/debug/clk/{nvcsi,vi,emc}/rate`) and compare
   with a baseline-tree capture if possible.
2. `journalctl -k` for `tegra_camera_platform` / clock or bandwidth errors.
3. Receive test as before: frame rate, CRC/ECC errors, dropped frames.

**Status:** open, NOT runtime-tested. Documentation only; no code or DT change.

## 4. Settle time after the register list (recorded 2026-10-13)

**Situation**
- The setfile moved out of the driver: `set_mode()` now writes one list
  (preset + `register_writes`) and waits 2 ms only after the whole list.
  Previously the 2 ms came right after the setfile, and
  extra `register_writes` followed it.
- For the default config the bus sequence is unchanged. With extra writes they
  now run without the 2 ms gap after the PLL registers of the setfile.

**To check**
1. Whether any register written after the PLL setup needs the PLL locked first
   (datasheet), and whether the list format needs a delay entry.
2. Receive test with `config/Delta_10_2000FPS_EvthMax.txt` after the change.

**Status:** open, deferred by decision; NOT runtime-tested.

## 5. Kernel WARNING on the stream start after a failed start (recorded 2026-10-13)

**Situation**
- When `set_mode()` fails (e.g. empty `nrv_register_writes` -> `-ENODATA`), the
  next STREAMON logs
  `WARNING ... v4l2-subdev.c:410` from `tegra_channel_set_stream()`.
- Cause (NVIDIA `vi/channel.c`): CSI is enabled before the sensor; when the sensor
  fails, the already enabled NVCSI subdev is not disabled again. Linux 6.8 warns
  when `.s_stream(1)` is called on an already enabled subdev.
- Effect measured on the Jetson: none on data. Streams after 0..3 failed starts all
  received 30/30 buffers (default and stress config); the former Python stress test 20/20.
  The kernel is tainted `W` and a call trace is logged.

**Decision:** keep the explicit failure (a silent start would give a stream that
never delivers data, with no reason). The SDK checks that the control holds a list
before STREAMON, so normal use never takes this path. A second NVIDIA patch
(disable CSI again on failure) is not planned.

**Status:** open (cosmetic), documented.


## 6. SDK Stream: USERPTR buffers need 64-byte alignment (recorded 2026-10-14, resolved 2026-10-15)

**Situation**
- `sdk/src/stream.cpp` now uses V4L2 USERPTR: the SDK allocates the 32 capture
  buffers as `std::vector<std::byte>` and swaps the filled buffer with the vector
  given to `Produce` (no copy from uncached DMA memory, which cost ~1 ms/MiB).
- VI rejects USERPTR addresses that are not 64-byte aligned. Measured on the Orin
  (`VIDIOC_QBUF` with offsets from a page-aligned block): 16 -> `EINVAL`,
  64 / 256 / 4096 -> ok.
- `std::vector<std::byte>` (default allocator, glibc) only guarantees 16 bytes; a
  1 MiB block is `mmap` + 16-byte header. So the `Stream` constructor currently
  fails with `VIDIOC_QBUF: Invalid argument` on the real device
  (`external_stress_test` fails). The dummy device (`NRV_DVS_DUMMY_DEVICE`) applies
  the same 64-byte check and reproduces it.

**Options**
1. Public buffer type with a 64-byte aligned allocator
   (`delta::Buffer = std::vector<std::byte, delta::AlignedAllocator<std::byte>>`) used
   by `Pipe::Read` / `Produce`; changes the public API (app and tests follow).
2. Keep `std::vector<std::byte>` and re-allocate unaligned memory inside the SDK;
   brings a copy back.

**Status:** resolved 2026-10-15. Pipe data is `std::vector<delta::Block>`
(`struct alignas(64) Block { std::array<std::byte, 64> bytes; }`, public
`nrv_dvs/pipe.h`), so every data buffer is 64-byte aligned and `Stream` swaps the
filled buffer out without a copy. Decoder output is rounded up to whole Blocks
(bytes after the last `EventGroup` carry no data). Measured: QBUF with a new
address each time (re-pin) ~190 us vs ~1 ms to copy 1 MiB out of the uncached MMAP
buffer; stress test Release 336.4 / Debug 334.8 buf/s, starved 0.

The pipe redesign (one pipe per Source, branch pipes without a queue, start on
attach, `shared_ptr` ownership) is agreed but not implemented yet.

## 7. CSI-2 data type: RAW8 (0x2A) instead of user-defined 0x31 (recorded 2026-10-19)

**Situation**
- The CSI-2 data type (sensor `0x3901`) is only a type indicator for the
  receiver; the payload format is selected separately by `0x3260` bit1
  (0 = compressed / Group AER, 1 = RAW2). The SDK decides the format from
  `0x3260`, never from the data type.
- The only reason for the VI patch (`0001-vi5-match-csi-user-datatype.patch`) is
  that stock VI does not accept the user-defined data type 0x31. With 0x2A stock
  VI stores the payload as RAW8 bytes, which is what the patch does for 0x31.
- RAW2 already uses 0x2A on the stock path (`raw2_production_start.sh`).
  Group AER with 0x2A has not been run.

**Decision (provisional):** matching the data type to the payload is not
required, and doing so is what forces a patch to NVIDIA tegra-camera code. Prefer
0x2A + stock modules once verified.

**To check**
1. Stock `tegra-camera.ko` (no VI patch), sensor `0x3901=0x2A`, `0x3260` bit1 = 0:
   long recording, CRC/ECC and dropped-buffer count 0.
2. Decoder health equal to the 0x31 + patch path for the same config (invalid /
   unknown words and frame gaps 0, same buf/s range). A byte-identical comparison
   is not possible (different scene per run); the payload bytes are the same by
   construction (only the packet header data type differs).

**Status (2026-10-19):** applied in the tree, not yet verified on the board. The VI
patch, the `tegra-camera.ko` build and its depmod override were removed;
every group AER setfile (`config/`, `sdk/settings/`, `app/dvs_streamer/settings/`)
now writes `3901=2A`; the sensor driver no longer has the `nrv_csi_datatype`
control or the reset data type readback (only the VI patch used them). A setfile
without `3901=2A` sends 0x31, which stock VI drops (capture timeout, no frames).
The last receive-tested state (VI patch + 0x31) is kept in the 2026-10-19 backup
(`nrv-dvs-jetson-orig-20261019.tar`). The debug VI module used until then is
recorded in issue 9.

## 8. Sensor RGB565 output mode undocumented (recorded 2026-10-19)

**Situation**
- The register map lists `0x3260` bit0 `DTAG_RGB_FRAME_r` (0: normal,
  1: RGB565 mode) next to bit1 `DTAG_RAW2_FRAME_r`. The preliminary datasheet
  (REV0.01) has nothing else on it: no packet format, data type, word count or
  frame size.
- The sensor has no color filter and each pixel only gives ON / OFF / none, so
  this cannot be a color image.
- Guess (unconfirmed): a preview frame of the event map, 16 bit per pixel with
  ON / OFF / none drawn in different colors (960x720x2 B ~ 1.38 MB per frame),
  displayable by a standard RGB565 receiver without an event decoder.

**Possible use:** bring-up or debugging on a new board or host, where a plain image
pipeline can show events before the event decoder works.

**To check**
1. Ask Samsung for the pixel mapping, data type, word count and frame size.
2. Or test: data type 0x2A, `0x3260=0x01`, dump the raw bytes. The VI mode
   size may not match the frame size, so frame errors are possible.

**Status:** TBD, not tested.

## 9. Debug VI module for capture status analysis (recorded 2026-10-19)

**Situation**
- From 2026-10-16 to 2026-10-19 the board ran debug builds of `tegra-camera.ko`
  (VI patch of issue 7 plus logging in the capture dequeue path of
  `vi/vi5_fops.c`), installed as
  `/lib/modules/6.8.12-1021-tegra/updates/nrv/tegra-camera.ko`:
  - v1 (2026-10-16, md5 `3e4421d4...`): the `corr_err` line also prints
    `status` and `notify_bits`. This proved the occasional dropped buffers to be
    CSI payload CRC errors (`status 2` CAPTURE_STATUS_CSIMUX_FRAME,
    `notify_bits 0x20000` = bit 17 PD_CRC_ERR, `err_data 4194402`).
  - v2 (2026-10-19, md5 `921057bf...`): v1 plus, for every SUCCESS frame with
    notify bits set, `nrv_dbg: success frame N notify_bits 0x...` (rate limited),
    and every 100000 frames `nrv_dbg: frames N success_with_notify M or_bits 0x...`.
    Observed: after a driver starve (no queued request) the next successful frame
    carries bit 46 CHANSEL_NO_MATCH (`0x400000000000`); a 4 h RAW2 recording
    (2026-10-19, 23.8 M buffers) showed it only around its 4 starved buffers.
- Stock VI logs only `corr_err: discarding frame N, flags: F, err_data E`.

**Possible use:** classifying receive errors (CRC vs ECC vs short frame vs
starve) and checking that SUCCESS frames carry no hidden error bits during long
runs. Not for production: it logs from the capture completion path.

**How to rebuild** (copies of both modules and the v2 source tree are in the
2026-10-19 backup, `extra/cache/nrv-corr-debug/` and `extra/cache/nrv-vi-debug2/`):
apply the diff below to the pristine `nvidia-oot` sources (`.cache/pristine/`;
it applies without the VI patch, which stock VI no longer needs with 0x2A), build
`tegra-camera.ko` as the removed `build.sh drivers` step did (camera `M=` build
with `srctree.nvidia-oot=`, `KBUILD_EXTRA_SYMBOLS=/usr/src/nvidia/nvidia-public/Module.symvers`,
`ccflags-y=-I<compat> -I<tree>/include -DCONFIG_V4L2_ASYNC -DCONFIG_V4L2_FWNODE
-DCONFIG_VIDEOBUF2_DMA_CONTIG`, target `tegra-camera.ko`), install it under
`updates/` with a depmod `override tegra-camera` and reboot (never hot-swap the
camera stack).

```diff
--- a/drivers/media/platform/tegra/camera/vi/vi5_fops.c
+++ b/drivers/media/platform/tegra/camera/vi/vi5_fops.c
@@ -578,9 +578,10 @@
 			} else {
 				dev_warn(vi->dev,
 					"corr_err: discarding frame %d, flags: %d, "
-					"err_data %d\n",
+					"err_data %d, status %u, notify_bits 0x%llx\n",
 					descr->status.frame_id, descr->status.flags,
-					descr->status.err_data);
+					descr->status.err_data, descr->status.status,
+					(unsigned long long)descr->status.notify_bits);
 				frame_err = true;
 			}
 		} else if (!vi_port) {
@@ -591,6 +592,26 @@
 			goto uncorr_err;
 		}
 
+		/* nrv debug: notify bits on successful frames + periodic summary */
+		{
+			static u64 dbg_frames, dbg_notify;
+			static u64 dbg_or;
+			u64 nb = descr->status.notify_bits;
+
+			dbg_frames++;
+			if (descr->status.status == CAPTURE_STATUS_SUCCESS && nb) {
+				dbg_notify++;
+				dbg_or |= nb;
+				dev_warn_ratelimited(vi->dev,
+					"nrv_dbg: success frame %u notify_bits 0x%llx\n",
+					descr->status.frame_id, (unsigned long long)nb);
+			}
+			if (dbg_frames % 100000 == 0)
+				dev_info(vi->dev,
+					"nrv_dbg: frames %llu success_with_notify %llu or_bits 0x%llx\n",
+					dbg_frames, dbg_notify, dbg_or);
+		}
+
 		spin_lock_irqsave(&chan->capture_state_lock, flags);
 		if (chan->capture_state != CAPTURE_ERROR) {
 			chan->capture_reqs_enqueued -= 1;
```

**Status:** recorded only; not part of the tree. The diff applies to the pristine
`vi5_fops.c` (`patch --dry-run` checked); that build is not compiled or tested.

