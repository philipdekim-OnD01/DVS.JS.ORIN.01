// SPDX-License-Identifier: GPL-2.0-only
/*
 * nrv_register_writes V4L2 control (ABI: tegra-cam/register_control.h).
 *
 * Registered from the subdev .registered internal op, i.e. before VI copies
 * the subdev control handler into /dev/video* at bind (a control added after
 * tegracam_v4l2subdev_register() returns would reach only /dev/v4l-subdev*).
 * Not added to tegracam's TEGRA_CAMERA_CID_* table; tegracam owns and frees
 * the handler.
 *
 * Lifetime: dvs_remove() clears dvs->register_ctrl before
 * tegracam_v4l2subdev_unregister() frees the handler, so the helpers below
 * (READ_ONCE) then take the no-control path instead of locking freed memory.
 *
 * Locking: the callbacks run with the framework's control handler lock held.
 * They only validate/store; they never touch I2C, the reset GPIO or the
 * receiver. dvs->lock stays private to the dvs_i2c_*() helpers.
 */
#include "tegra-cam/register_control.h"
#include "dvs/dvs.h"
#include <linux/compiler.h>
#include <linux/container_of.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <media/camera_common.h>
#include <media/tegracam_core.h>
#include <media/v4l2-ctrls.h>
#include <media/v4l2-subdev.h>

/*
 * Whole payload: the sole empty marker, or 1..4096 words with bits 31:24 = 0
 * and no stream on/off register.
 */
static int dvs_register_words_valid(const u32 *words, u32 n)
{
	u32 i;

	if (n == 0 || n > DVS_MAX_REGISTER_WRITES)
		return -EINVAL;
	if (n == 1 && words[0] == DVS_REGISTER_WRITES_EMPTY)
		return 0;
	for (i = 0; i < n; i++)
		if ((words[i] & DVS_REGISTER_WORD_RESERVED) ||
		    DVS_REGISTER_WORD_ADDR(words[i]) == DVS_REG_STREAM)
			return -EINVAL;
	return 0;
}

static int dvs_register_try_ctrl(struct v4l2_ctrl *ctrl)
{
	if (ctrl->id != V4L2_CID_NRV_REGISTER_WRITES)
		return -EINVAL;
	return dvs_register_words_valid(ctrl->p_new.p_u32, ctrl->new_elems);
}

/*
 * Store only. Also called by v4l2_ctrl_handler_setup() at VI bind with the
 * default (empty marker), when the sensor may not even be verified yet.
 */
static int dvs_register_s_ctrl(struct v4l2_ctrl *ctrl)
{
	struct dvs_data *dvs = ctrl->priv;
	const u32 *words = ctrl->p_new.p_u32;
	u32 n = ctrl->new_elems;
	int ret;

	if (ctrl->id != V4L2_CID_NRV_REGISTER_WRITES || !dvs)
		return -EINVAL;
	if (READ_ONCE(dvs->stopping))
		return -ENODEV;
	/* EXECUTE_ON_WRITE: an unchanged list written while streaming too. */
	if (READ_ONCE(dvs->streaming))
		return -EBUSY;
	ret = dvs_register_words_valid(words, n);
	if (ret)
		return ret;
	if (n == 1 && words[0] == DVS_REGISTER_WRITES_EMPTY) {
		dvs->registers.count = 0;
		return 0;
	}
	memcpy(dvs->registers.words, words, n * sizeof(*words));
	dvs->registers.count = n;
	return 0;
}

static const struct v4l2_ctrl_ops dvs_register_ctrl_ops = {
	.try_ctrl = dvs_register_try_ctrl,
	.s_ctrl = dvs_register_s_ctrl,
};

static const struct v4l2_ctrl_config dvs_register_ctrl_config = {
	.ops = &dvs_register_ctrl_ops,
	.id = V4L2_CID_NRV_REGISTER_WRITES,
	.name = DVS_REGISTER_CTRL_NAME,
	.type = V4L2_CTRL_TYPE_U32,
	.flags = V4L2_CTRL_FLAG_DYNAMIC_ARRAY | V4L2_CTRL_FLAG_EXECUTE_ON_WRITE,
	.min = 0,
	.max = 0xffffffffULL,
	.step = 1,
	.def = DVS_REGISTER_WRITES_EMPTY,
	.dims = { DVS_MAX_REGISTER_WRITES },
	/* Must stay 0: VI inherits only non-private subdev controls. */
	.is_private = 0,
};

static int dvs_subdev_registered(struct v4l2_subdev *sd)
{
	struct camera_common_data *s =
		container_of(sd, struct camera_common_data, subdev);
	struct dvs_data *dvs = tegracam_get_privdata(to_tegracam_device(s));
	struct v4l2_ctrl *ctrl;

	if (!dvs || !sd->ctrl_handler)
		return -ENODEV;
	/* Re-registration keeps the existing control and the stored list. */
	if (READ_ONCE(dvs->register_ctrl))
		return 0;
	ctrl = v4l2_ctrl_new_custom(sd->ctrl_handler, &dvs_register_ctrl_config,
				    dvs);
	if (sd->ctrl_handler->error)
		return sd->ctrl_handler->error;
	WRITE_ONCE(dvs->register_ctrl, ctrl);
	return 0;
}

const struct v4l2_subdev_internal_ops dvs_internal_ops = {
	.registered = dvs_subdev_registered,
};

int dvs_register_snapshot(struct dvs_data *dvs, u32 **words, u32 *count)
{
	struct v4l2_ctrl *ctrl = READ_ONCE(dvs->register_ctrl);
	u32 *copy = NULL;
	u32 n;

	*words = NULL;
	*count = 0;
	/* No control (VI not bound yet, or cleared by dvs_remove() before
	 * tegracam frees the handler): empty list, set_mode() refuses. */
	if (!ctrl)
		return 0;
	v4l2_ctrl_lock(ctrl);
	n = dvs->registers.count;
	if (n)
		copy = kmemdup(dvs->registers.words, n * sizeof(*copy),
			       GFP_KERNEL);
	v4l2_ctrl_unlock(ctrl);
	if (n && !copy)
		return -ENOMEM;
	*words = copy;
	*count = n;
	return 0;
}

void dvs_register_set_streaming(struct dvs_data *dvs, bool streaming)
{
	struct v4l2_ctrl *ctrl = READ_ONCE(dvs->register_ctrl);

	if (ctrl)
		v4l2_ctrl_lock(ctrl);
	WRITE_ONCE(dvs->streaming, streaming);
	if (ctrl)
		v4l2_ctrl_unlock(ctrl);
}
