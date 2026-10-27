#include "tegra-cam/camera_common_sensor_ops.h"
#include "tegra-cam/register_control.h"
#include "dvs/i2c.h"
#include <linux/delay.h>
#include <linux/module.h>
#include <linux/slab.h>

/*
 * The driver holds no sensor configuration: set_mode() writes only the list
 * stored in nrv_register_writes (set from userspace) and refuses to stream
 * without one. The list must make the sensor send RAW8 (0x2A), the data type
 * stock VI matches for the SRGGB8 format of every mode.
 */

static const int frame_rates[] = { 30 };
/* CSI geometry of the VI buffer only, unrelated to the event coordinate
 * resolution. Entry i selects DT mode<i>; the register list must make the
 * sensor send frames of that size. */
static const struct camera_common_frmfmt formats[] = {
	{ { 8192, 128 }, frame_rates, 1, false, 0 },
	{ { 2880, 61 }, frame_rates, 1, false, 1 },
};

static int write_reg(struct camera_common_data *s, u16 addr, u8 value)
{
	struct dvs_data *dvs = tegracam_get_privdata(to_tegracam_device(s));
	if (!dvs)
		return -ENODEV;
	return dvs_i2c_send(dvs, addr, value);
}

static int read_reg(struct camera_common_data *s, u16 addr, u8 *value)
{
	struct dvs_data *dvs = tegracam_get_privdata(to_tegracam_device(s));
	if (!dvs)
		return -ENODEV;
	return dvs_i2c_recv(dvs, addr, value);
}

/* Checked against the tegracam_set_privdata store offset in the installed
 * tegra-camera.ko. */
static_assert(offsetof(struct camera_common_data, priv) == 592);

/*
 * Receiver timing (pixel_clock/mipi_clock, settle, lane polarity) comes only
 * from the DT mode0 node (cam1.dts) as parsed by sensor_common.c; the driver
 * no longer modifies the parsed signal properties.
 */

static int power_on(struct camera_common_data *s)
{
	/* No reset per V4L2 power call; dvs_check_work() and set_mode() reset
	 * the sensor. */
	s->power->state = SWITCH_ON;
	return 0;
}

static int power_off(struct camera_common_data *s)
{
	int ret = write_reg(s, DVS_REG_STREAM, 0x00);
	s->power->state = SWITCH_OFF;
	return ret;
}

static struct camera_common_pdata *parse_dt(struct tegracam_device *tc)
{
	/* The board has its own oscillator; the CAM1_MCLK pin is not requested
	 * as a clock. */
	return devm_kzalloc(tc->dev, sizeof(struct camera_common_pdata),
			    GFP_KERNEL);
}

static int power_get(struct tegracam_device *tc)
{
	/* Board power is supplied externally; no regulator or SoC MCLK control. */
	tc->s_data->power->state = SWITCH_OFF;
	return 0;
}

static int power_put(struct tegracam_device *tc)
{
	return 0;
}

static int set_mode(struct tegracam_device *tc)
{
	struct camera_common_data *s = tc->s_data;
	struct dvs_data *priv = tegracam_get_privdata(tc);
	u32 *writes = NULL, count = 0, i;
	int ret;

	/* dvs_check_work() owns the sensor until it has verified it. */
	if (!READ_ONCE(priv->ready)) {
		dev_err(tc->dev, "sensor not ready (connection check pending)\n");
		return READ_ONCE(priv->stopping) ? -ENODEV : -EAGAIN;
	}
	/*
	 * Snapshot of nrv_register_writes before touching the sensor. A control
	 * update after this point is stored for the next set_mode() and is not
	 * spliced into this sequence.
	 */
	ret = dvs_register_snapshot(priv, &writes, &count);
	if (ret)
		return ret;
	if (!count) {
		dev_err(tc->dev, "register sensor settings\n");
		return -ENODATA;
	}
	/* Start every configuration from the same reset state so no earlier
	 * settings remain. */
	dvs_reset(priv, true);
	ret = write_reg(s, DVS_REG_STREAM, 0x00);
	if (ret)
		goto out;
	/* nrv_register_writes, in order (stream on/off is rejected there). */
	for (i = 0; i < count; i++) {
		ret = write_reg(s, DVS_REGISTER_WORD_ADDR(writes[i]),
				DVS_REGISTER_WORD_VALUE(writes[i]));
		if (ret)
			goto fail;
	}
	/* Settle time after the list (placement to be revisited, OPEN-ISSUES). */
	msleep(2);
	dev_info(tc->dev, "%u register write(s) applied\n", count);
	kfree(writes);
	return 0;
fail:
	write_reg(s, DVS_REG_STREAM, 0x00);
	dev_err(tc->dev, "mode configuration failed: %d\n", ret);
out:
	kfree(writes);
	return ret;
}

static int start_streaming(struct tegracam_device *tc)
{
	struct dvs_data *priv = tegracam_get_privdata(tc);
	int ret;

	/* Published first so nrv_register_writes returns -EBUSY from here on. */
	dvs_register_set_streaming(priv, true);
	ret = write_reg(tc->s_data, DVS_REG_STREAM, 0x01);
	if (ret)
		dvs_register_set_streaming(priv, false);
	return ret;
}

static int stop_streaming(struct tegracam_device *tc)
{
	struct dvs_data *priv = tegracam_get_privdata(tc);
	int ret = write_reg(tc->s_data, DVS_REG_STREAM, 0x00);
	dvs_register_set_streaming(priv, false);
	return ret;
}

struct camera_common_sensor_ops sensor_ops = {
	.numfrmfmts = ARRAY_SIZE(formats),
	.frmfmt_table = formats,
	.power_on = power_on,
	.power_off = power_off,
	.write_reg = write_reg,
	.read_reg = read_reg,
	.parse_dt = parse_dt,
	.power_get = power_get,
	.power_put = power_put,
	.set_mode = set_mode,
	.start_streaming = start_streaming,
	.stop_streaming = stop_streaming,
};
