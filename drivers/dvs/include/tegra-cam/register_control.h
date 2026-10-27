/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * nrv_register_writes: the generic sensor register control of nrv_dvs.
 *
 * ABI (README "Sensor register control"):
 *   CID   V4L2_CID_USER_BASE + 0x2000 = 0x00982900 (driver-private offset,
 *         clear of every per-driver base reserved in the 6.8 v4l2-controls.h;
 *         re-audit on BSP upgrades)
 *   type  V4L2_CTRL_TYPE_U32, DYNAMIC_ARRAY, max 4096 elements
 *   word  (addr << 8) | value: bits 23:8 = u16 register address,
 *         bits 7:0 = u8 value, bits 31:24 must be 0. Order and duplicates are
 *         kept: the words are written in array order.
 *   empty the sole word 0xffffffff (also the default). Linux 6.8 rejects
 *         zero-byte dynamic arrays, so a logical empty list needs a marker.
 *         It is valid only alone and is never written to the sensor.
 *   The stream on/off register (DVS_REG_STREAM) is rejected: the driver alone
 *   starts and stops streaming.
 *
 * s_ctrl only stores the list (-EBUSY while streaming). The driver has no
 * built-in sensor configuration: set_mode() fails with -ENODATA while the list
 * is empty, otherwise it resets the sensor and writes a snapshot of the list.
 */
#ifndef DVS_REGISTER_CONTROL_H
#define DVS_REGISTER_CONTROL_H
#include <linux/types.h>
#include <linux/v4l2-controls.h>

struct dvs_data;
struct v4l2_subdev_internal_ops;

#define V4L2_CID_NRV_REGISTER_WRITES (V4L2_CID_USER_BASE + 0x2000)
#define DVS_REGISTER_CTRL_NAME "nrv_register_writes"
#define DVS_MAX_REGISTER_WRITES 4096U
#define DVS_REGISTER_WRITES_EMPTY 0xffffffffU
#define DVS_REGISTER_WORD_RESERVED 0xff000000U
#define DVS_REGISTER_WORD_ADDR(w) ((u16)(((w) >> 8) & 0xffff))
#define DVS_REGISTER_WORD_VALUE(w) ((u8)((w) & 0xff))

/* Per-device stored list; count == 0 is the logical empty list. */
struct dvs_register_list {
	u32 count;
	u32 words[DVS_MAX_REGISTER_WRITES];
};

/* Set as tc->v4l2sd_internal_ops; .registered creates the control. */
extern const struct v4l2_subdev_internal_ops dvs_internal_ops;

/*
 * Copy the stored list under the control handler lock. *words is NULL when
 * the list is empty, else a kmalloc'ed copy the caller must kfree().
 */
int dvs_register_snapshot(struct dvs_data *dvs, u32 **words, u32 *count);

/* Publish the streaming flag under the control handler lock. */
void dvs_register_set_streaming(struct dvs_data *dvs, bool streaming);

#endif
