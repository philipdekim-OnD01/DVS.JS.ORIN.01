#include "dvs/i2c.h"
#include "tegra-cam/camera_common_sensor_ops.h"
#include "tegra-cam/register_control.h"
#include <linux/container_of.h>
#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/jiffies.h>
#include <linux/kref.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/regmap.h>
#include <linux/slab.h>
#include <linux/workqueue.h>

#include <linux/types.h>

// makes i2c address
#define MAKE_I2C_ADDR(addr)                \
	{                                  \
		(addr) >> 8, (addr) & 0xff \
	}
#define I2C_READ(dev_addr, reg_addr, dst, count)                       \
	{ .addr = (dev_addr),                                          \
	  .flags = !I2C_M_RD,                                          \
	  .len = 2,                                                    \
	  .buf = (reg_addr) },                                         \
	{                                                              \
		.addr = (dev_addr), .flags = I2C_M_RD, .len = (count), \
		.buf = (dst)                                           \
	}
#define I2C_WRITE(dev_addr, reg_addr, ...)                                    \
	{                                                                     \
		.addr = (dev_addr), .flags = !I2C_M_RD,                       \
		.len = sizeof(                                                \
			(u8[]){ (reg_addr)[0], (reg_addr)[1], __VA_ARGS__ }), \
		.buf = ((u8[]){ (reg_addr)[0], (reg_addr)[1], __VA_ARGS__ })  \
	}

/*
 * The only places that take dvs->lock: one I2C transfer each. Everything else
 * (sensor ops, set_mode(), dvs_check_work(), probe/remove) goes through these.
 * Return 0 or a negative errno; -ENODEV once dvs->stopping is set.
 */
int dvs_i2c_send(struct dvs_data *dvs, u16 reg, u8 value)
{
	u8 address[] = MAKE_I2C_ADDR(reg);
	struct i2c_msg msg[] = { I2C_WRITE(dvs->client->addr, address, value) };
	int ret;

	mutex_lock(&dvs->lock);
	if (READ_ONCE(dvs->stopping)) {
		ret = -ENODEV;
		goto out;
	}
	ret = i2c_transfer(dvs->client->adapter, msg, 1);
	if (ret == 1) {
		ret = 0;
	} else {
		dev_err(&dvs->client->dev, "i2c write 0x%04x failed: %d\n", reg,
			ret);
		ret = ret < 0 ? ret : -EIO;
	}
out:
	mutex_unlock(&dvs->lock);
	return ret;
}

int dvs_i2c_recv(struct dvs_data *dvs, u16 reg, u8 *value)
{
	u8 address[] = MAKE_I2C_ADDR(reg);
	struct i2c_msg msg[] = { I2C_READ(dvs->client->addr, address, value,
					  1) };
	int ret;

	mutex_lock(&dvs->lock);
	if (READ_ONCE(dvs->stopping)) {
		ret = -ENODEV;
		goto out;
	}
	ret = i2c_transfer(dvs->client->adapter, msg, 2);
	if (ret == 2) {
		ret = 0;
	} else {
		dev_err(&dvs->client->dev, "i2c read 0x%04x failed: %d\n", reg,
			ret);
		ret = ret < 0 ? ret : -EIO;
	}
out:
	mutex_unlock(&dvs->lock);
	return ret;
}

int dvs_i2c_transfer(struct dvs_data *dvs, struct i2c_msg *msg, int count)
{
	int ret;

	if (count <= 0)
		return -EINVAL;
	mutex_lock(&dvs->lock);
	if (READ_ONCE(dvs->stopping)) {
		ret = -ENODEV;
		goto out;
	}
	ret = i2c_transfer(dvs->client->adapter, msg, count);
	if (ret == count) {
		ret = 0;
	} else {
		dev_err(&dvs->client->dev, "i2c transfer failed: %d of %d\n",
			ret, count);
		ret = ret < 0 ? ret : -EIO;
	}
out:
	mutex_unlock(&dvs->lock);
	return ret;
}

/*
 * The only RSTN control. cam1.dts: reset-gpios = <&gpio 113 0>
 * (GPIO_ACTIVE_HIGH), so the logical value is the pin level and RSTN is
 * active low: 0 = reset asserted, 1 = released.
 *
 * release = false: assert and leave asserted (standby until the next reset).
 * release = true:  full reset cycle with the validated timing, RSTN low 10 ms,
 *                  release, then 100 ms before the first I2C access.
 */
void dvs_reset(struct dvs_data *dvs, bool release)
{
	gpiod_set_value_cansleep(dvs->reset_gpio, 0);
	if (!release)
		return;
	msleep(10);
	gpiod_set_value_cansleep(dvs->reset_gpio, 1);
	msleep(100);
}

static const struct regmap_config dvs_regmap_config = {
	.reg_bits = 16,
	.val_bits = 8,
	.cache_type = REGCACHE_NONE,
};

/*
 * Connection procedure (was in probe): reset the sensor, verify that it answers
 * on I2C with a write/readback of DVS_REG_CONNECTION_CHECK, then mark it ready. On failure RSTN
 * stays asserted and the check is retried after 2, 4, 8, 16, 30, 30... s.
 * It only runs while !ready and set_mode() only runs once ready, so the two
 * reset sequences never overlap. cancel_delayed_work_sync() in dvs_stop()
 * also handles the self-requeue below.
 */
static void dvs_check_work(struct work_struct *work)
{
	struct dvs_data *dvs = container_of(to_delayed_work(work),
					    struct dvs_data, check_work);
	struct device *dev = &dvs->client->dev;
	/* Alternate the pattern so a stuck bus/register cannot pass twice. */
	static u8 pattern = 0x55; /* 0b01010101 */
	u8 readback = 0;
	int ret;

	if (READ_ONCE(dvs->stopping) || READ_ONCE(dvs->ready))
		return;

	dvs_reset(dvs, true);

	ret = dvs_i2c_send(dvs, DVS_REG_CONNECTION_CHECK, pattern);
	if (!ret)
		ret = dvs_i2c_recv(dvs, DVS_REG_CONNECTION_CHECK, &readback);
	if (!ret && readback != pattern)
		ret = -EIO;
	if (ret) {
		if (dvs->retry_delay_ms == DVS_RETRY_INITIAL_MS)
			dev_warn(
				dev,
				"DVS not ready (wrote=0x%02x read=0x%02x ret=%d); retrying\n",
				pattern, readback, ret);
		dev_dbg(dev,
			"I2C readback: wrote=0x%02x read=0x%02x ret=%d; retry in %u ms\n",
			pattern, readback, ret, dvs->retry_delay_ms);
		/* Keep RSTN asserted until the next attempt. */
		dvs_reset(dvs, false);
		if (!READ_ONCE(dvs->stopping))
			schedule_delayed_work(
				&dvs->check_work,
				msecs_to_jiffies(dvs->retry_delay_ms));
		dvs->retry_delay_ms =
			min(dvs->retry_delay_ms * 2U, DVS_RETRY_MAX_MS);
		return;
	}

	dvs->retry_delay_ms = DVS_RETRY_INITIAL_MS;
	/* A register readback is not a chip ID check. */
	dev_info(dev,
		 "DVS ready: I2C readback wrote=0x%02x read=0x%02x; silicon ID not verified\n",
		 pattern, readback);
	/* Last step: from here on set_mode() may reset/configure the sensor. */
	WRITE_ONCE(dvs->ready, true);
}

/*
 * After tegracam_v4l2subdev_register(), v4l2_i2c_subdev_init() has replaced the
 * I2C client data with the v4l2_subdev (camera_common relies on that through
 * to_camera_common_data()), so never use i2c_get_clientdata() for dvs_data:
 * go through the tegracam private data instead.
 */
static struct dvs_data *dvs_from_client(struct i2c_client *client)
{
	struct camera_common_data *s = to_camera_common_data(&client->dev);

	if (!s)
		return NULL;
	return tegracam_get_privdata(to_tegracam_device(s));
}

int dvs_probe(struct i2c_client *client)
{
	struct device *dev = &client->dev;
	struct tegracam_device *tc;
	struct dvs_data *dvs;
	int ret;

	if (!dev->of_node)
		return -EINVAL;
	if (!i2c_check_functionality(client->adapter, I2C_FUNC_I2C))
		return dev_err_probe(
			dev, -EOPNOTSUPP,
			"Adapter does not support plain I2C transfers\n");

	/* Everything dvs_i2c_*() and the work item touch is set up first. */
	dvs = kzalloc(sizeof(*dvs), GFP_KERNEL);
	if (!dvs)
		return -ENOMEM;
	kref_init(&dvs->ref);
	mutex_init(&dvs->lock);
	INIT_DELAYED_WORK(&dvs->check_work, dvs_check_work);
	dvs->client = client;
	dvs->retry_delay_ms = DVS_RETRY_INITIAL_MS;
	dvs->stopping = false;
	dvs->ready = false;
	dvs->streaming = false;

	tc = devm_kzalloc(dev, sizeof(*tc), GFP_KERNEL);
	if (!tc) {
		ret = -ENOMEM;
		goto err_put;
	}
	dvs->tc = tc;

	/* Hold RSTN asserted (pin low, see dvs_reset()) until
	 * dvs_check_work() runs the connection procedure. */
	dvs->reset_gpio = devm_gpiod_get(dev, "reset", GPIOD_OUT_LOW);
	if (IS_ERR(dvs->reset_gpio)) {
		ret = dev_err_probe(dev, PTR_ERR(dvs->reset_gpio),
				    "reset GPIO unavailable\n");
		goto err_put;
	}

	tc->client = client;
	tc->dev = dev;
	strscpy(tc->name, "nrv_dvs", sizeof(tc->name));
	tc->dev_regmap_config = &dvs_regmap_config;
	tc->sensor_ops = &sensor_ops;
	ret = tegracam_device_register(tc);
	if (ret)
		goto err_put;
	tegracam_set_privdata(tc, dvs);

	/* The R39.2.1 DT parser does not accept a RAW8 pixel_t string, but the
	 * VI format table supports SRGGB8, so set the real capture format after
	 * parsing (every mode). The CSI signal depth (8) comes from DT; the Bayer
	 * ISP path is not used. */
	for (u32 i = 0; i < tc->s_data->sensor_props.num_modes; i++)
		tc->s_data->sensor_props.sensor_modes[i]
			.image_properties.pixel_format = V4L2_PIX_FMT_SRGGB8;
	tc->s_data->colorfmt = camera_common_find_pixelfmt(V4L2_PIX_FMT_SRGGB8);
	if (!tc->s_data->colorfmt) {
		ret = -EINVAL;
		goto err_unregister;
	}

	/*
	 * nrv_register_writes is created from the subdev .registered op, which
	 * runs when VI binds the subdev (possibly inside the call below) and
	 * before VI copies the control handler to /dev/video*. privdata is
	 * already set, so the callback can find dvs.
	 */
	tc->v4l2sd_internal_ops = &dvs_internal_ops;
	ret = tegracam_v4l2subdev_register(tc, true);
	if (ret)
		goto err_unregister;

	/*
	 * The V4L2 subdev exists from here on; the sensor becomes usable once
	 * dvs_check_work() has verified it (set_mode() returns -EAGAIN before).
	 */
	schedule_delayed_work(&dvs->check_work, 0);
	dev_info(dev, "DVS driver loaded; connection check scheduled\n");
	return 0;

err_unregister:
	tegracam_device_unregister(tc);
err_put:
	dvs_put(dvs);
	return ret;
}

/*
 * Put a verified sensor into SW standby, then block further I2C access
 * (dvs_i2c_*() return -ENODEV) and stop the connection work item.
 */
static void dvs_stop(struct dvs_data *dvs)
{
	if (READ_ONCE(dvs->ready))
		dvs_i2c_send(dvs, 0x0100, 0x00);
	WRITE_ONCE(dvs->stopping, true);
	WRITE_ONCE(dvs->ready, false);

	/* Never wait for the worker while holding its mutex. */
	cancel_delayed_work_sync(&dvs->check_work);
}

void dvs_shutdown(struct i2c_client *client)
{
	struct dvs_data *dvs = dvs_from_client(client);

	if (dvs)
		dvs_stop(dvs);
}

void dvs_remove(struct i2c_client *client)
{
	struct dvs_data *dvs = dvs_from_client(client);

	if (!dvs) {
		dev_err(&client->dev,
			"private context unavailable during remove\n");
		return;
	}

	/* Standby + stop I2C and the work item first; V4L2 power_off during
	 * unregister then gets -ENODEV, which is harmless. */
	dvs_stop(dvs);
	/* The handler (and nrv_register_writes) is freed by the unregister
	 * below; later snapshot/set_streaming calls must not lock it. */
	WRITE_ONCE(dvs->register_ctrl, NULL);
	tegracam_v4l2subdev_unregister(dvs->tc);
	tegracam_device_unregister(dvs->tc);

	// Power-down sequence: drive RSTN low before removal to put the sensor
	// in hardware standby.
	// TBD: make sure I2C and CSI traffic have finished before RSTN goes low.
	dvs_reset(dvs, false);
	dvs_put(dvs);
	dev_info(&client->dev, "DVS driver removed\n");
}
