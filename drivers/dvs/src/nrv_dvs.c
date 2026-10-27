// SPDX-License-Identifier: GPL-2.0-only
/*
 * NRV FX20 sensor board DVS camera driver for L4T R39.2.1. Event bytes are
 * delivered unchanged in RAW8 (SRGGB8) VI buffers. The sensor configuration is
 * not part of the driver; it is loaded from userspace through the
 * nrv_register_writes control before streaming.
 *
 * Module entry and driver registration only (match tables, i2c_driver); the
 * module has no parameters. Probe/remove and I2C access: dvs/i2c.c. Sensor
 * ops: tegra-cam/camera_common_sensor_ops.c. The nrv_register_writes V4L2
 * control: tegra-cam/register_control.c. Receiver timing comes from the DT
 * mode0 node.
 */
#include "dvs/i2c.h"
#include <linux/i2c.h>
#include <linux/mod_devicetable.h>
#include <linux/module.h>

/* Must match the compatible string of the sensor node in cam1.dts. */
static const struct of_device_id dvs_of_match[] = {
	{ .compatible = "nrv,fx20-sensor-board" },
	{}
};
MODULE_DEVICE_TABLE(of, dvs_of_match);

static const struct i2c_device_id dvs_id[] = { { "nrv_dvs", 0 }, {} };
MODULE_DEVICE_TABLE(i2c, dvs_id);

static struct i2c_driver dvs_driver = {
	.driver = { .name = "nrv_dvs", .of_match_table = dvs_of_match },
	.probe = dvs_probe,
	.remove = dvs_remove,
	.shutdown = dvs_shutdown,
	.id_table = dvs_id,
};
module_i2c_driver(dvs_driver);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("NRV FX20 sensor board DVS camera driver");
