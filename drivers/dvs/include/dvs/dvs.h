#ifndef DVS_H
#define DVS_H
#include "tegra-cam/register_control.h"
#include <linux/miscdevice.h>
#include <linux/workqueue.h>

#define DVS_RETRY_INITIAL_MS 2000U
#define DVS_RETRY_MAX_MS 30000U
/* Stream on/off; only the driver writes it (rejected in nrv_register_writes). */
#define DVS_REG_STREAM 0x0100
/* Read/write register used only to verify that the sensor answers on I2C. */
#define DVS_REG_CONNECTION_CHECK 0x303B

struct v4l2_ctrl;

struct dvs_data {
	struct kref ref;
	struct tegracam_device *tc;
	struct i2c_client *client;
	struct gpio_desc *reset_gpio;
	struct delayed_work check_work;

	struct mutex lock;
	unsigned int retry_delay_ms;
	bool stopping;
	// This represents software standby state
	bool ready;
	// Published under the register_ctrl handler lock (register_control.c)
	bool streaming;

	// nrv_register_writes control and its stored list (handler lock).
	// register_ctrl is cleared by dvs_remove() before tegracam frees the
	// handler; read it with READ_ONCE().
	struct v4l2_ctrl *register_ctrl;
	struct dvs_register_list registers;
};

// Refcount Management, Refcount++
void dvs_get(struct dvs_data *dvs);
// Refcount Management, Refcount--
void dvs_put(struct dvs_data *dvs);

#endif
