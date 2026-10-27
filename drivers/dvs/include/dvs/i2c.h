#ifndef DVS_I2C_H
#define DVS_I2C_H
#include "dvs.h"
#include <linux/i2c.h>
#include <media/tegracam_core.h>

// clang-format off
// Driver callbacks; registered by the i2c_driver in nrv_dvs.c.
int  dvs_probe   (struct i2c_client *client);
void dvs_remove  (struct i2c_client *client);
void dvs_shutdown(struct i2c_client *client);
// clang-format on

struct i2c_batch {
	u16 reg;
	u8 value;
	bool write;
};

// clang-format off
// The only users of dvs->lock (one transfer each). Never call with it held.
int dvs_i2c_send    (struct dvs_data *dvs, u16 reg, u8 value);
int dvs_i2c_recv    (struct dvs_data *dvs, u16 reg, u8 *value);
int dvs_i2c_transfer(struct dvs_data *dvs, struct i2c_msg *ops, int count);
// Assert RSTN; with release, hold it and release it again (timing in i2c.c).
void dvs_reset       (struct dvs_data *dvs, bool release);
// clang-format on

#endif