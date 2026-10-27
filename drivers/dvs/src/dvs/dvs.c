// SPDX-License-Identifier: GPL-2.0-only
/* Lifetime of struct dvs_data (kref): freed when the last reference is put. */
#include "dvs/dvs.h"
#include <linux/kref.h>
#include <linux/slab.h>

static void dvs_free(struct kref *ref)
{
	struct dvs_data *dvs = container_of(ref, struct dvs_data, ref);
	kfree(dvs);
}

void dvs_get(struct dvs_data *dvs)
{
	kref_get(&dvs->ref);
}

void dvs_put(struct dvs_data *dvs)
{
	kref_put(&dvs->ref, dvs_free);
}
