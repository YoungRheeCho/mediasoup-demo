#include "rdma.h"

struct rdma_device *rdma_dev;

static int rdma_open_device(int is_server)
{
	int i, ret;

    rdma_dev = (struct rdma_device *) calloc(1, sizeof(struct rdma_device));
    if (!rdma_dev) {
        printf("calloc failed\n");
        return -1;
    }

    for (i = 0; i < NUM_QUEUES; i++) {
		rdma_dev->queues[i] = (struct rdma_queue *) 
			calloc(1, sizeof(struct rdma_queue));
		if (!rdma_dev->queues[i]) {
			printf("calloc failed\n");
			goto clean_dev;
		}

        rdma_dev->queues[i]->rdma_dev = rdma_dev;
    }

	/* Create event channel for RDMA connection
	   Server only needs a channel,
	   but client needs each channels for each connections.
     */
	int nr_channel = (is_server == true) ? 1 : NUM_QUEUES;
	for (i = 0; i < nr_channel; i++) {
		rdma_dev->ec[i] = rdma_create_event_channel();
		if (!rdma_dev->ec[i]) {
			printf("rdma_create_event_channel failed\n");
			goto clean_queues;
		}
	}
		
	/* Create event channel id */
	for (i = 0; i < nr_channel; i++) {
		ret = rdma_create_id(rdma_dev->ec[i],
				&rdma_dev->cm_id[i], NULL, RDMA_PS_TCP);
		if (ret) {
			printf("rdma_create_id failed\n");
			goto close_ec;
		}
	}

    return 0;

close_ec:
	for (i = 0; i < nr_channel; i++)
		rdma_destroy_event_channel(rdma_dev->ec[i]);
clean_queues:
	for (i = 0; i < nr_channel; i++) 
		free(rdma_dev->queues[i]);
clean_dev:
	free(rdma_dev);
	return -1;
}

static int rdma_create_device(struct rdma_queue *q)
{
	if (q->rdma_dev->pd) {
		return 0;
	}

	/* Create protect domain 
	   Protect domain manages qp, cq, mr, etc.
	 */
    q->rdma_dev->verbs = q->cm_id->verbs;
    q->rdma_dev->pd = ibv_alloc_pd(q->rdma_dev->verbs);
    if (!q->rdma_dev->pd) {
        printf("ibv_allod_pd failed\n");
        return -1;
    }

    return 0;
}

void rdma_close_device(void)
{
	for (int i = 0; i < NUM_QUEUES;i ++) {
		if (rdma_dev->cm_id[i]) {
			rdma_destroy_id(rdma_dev->cm_id[i]);
		}

		if (rdma_dev->ec[i]) {
			rdma_destroy_event_channel(rdma_dev->ec[i]);
		}

		if (rdma_dev->queues[i]) {
			free(rdma_dev->queues[i]);
		}
	}

	free(rdma_dev);
}

static int rdma_create_queue(struct rdma_queue *q, 
		struct ibv_comp_channel *cc, int c)
{
	if (cc) {
		goto create_cq;
	}
	
	/* Create completion channel */
	cc = ibv_create_comp_channel(q->cm_id->verbs);
	if (!cc) {
		printf("ibv_create_comp_channel failed\n");
		return -1;
	}

	/* Create completion queue */
create_cq:
    q->cq = ibv_create_cq(q->cm_id->verbs, CQ_CAPACITY, NULL, cc, 0);
    if (!q->cq) {
		printf("ibv_create_cq failed\n");
		goto close_cc;
	}
    
	int ret = ibv_req_notify_cq(q->cq, 0);
	if (ret) {
		printf("ibv_req_notify_cq failed\n");
		goto close_cq;
	}

	/* Create queue pair */
    struct ibv_qp_init_attr qp_attr = {};
    qp_attr.send_cq = q->cq;
    qp_attr.recv_cq = q->cq;
    qp_attr.qp_type = IBV_QPT_RC;
    qp_attr.cap.max_send_wr = c ? MAX_SGE : 64;
    qp_attr.cap.max_recv_wr = c ? MAX_SGE : 64;
    qp_attr.cap.max_send_sge = c ? MAX_WR : 32;
    qp_attr.cap.max_recv_sge = c ? MAX_WR : 32;

    ret = rdma_create_qp(q->cm_id, q->rdma_dev->pd, &qp_attr);
	if (ret) {
		printf("rdma_create_qp failed\n");
		goto close_cq;
	}
    q->qp = q->cm_id->qp;

    return 0;

close_cc:
	ibv_destroy_comp_channel(cc);
close_cq:
	ibv_destroy_cq(q->cq);
	return ret;
}

static struct ibv_mr *rdma_create_mr(struct ibv_pd *pd, size_t length)
{
	/* Create local buffer */
	rdma_dev->buf = (char *) calloc(1, length);
	if (!rdma_dev->buf) {
		printf("calloc failed\n");
		return NULL;
	}

	/* Register RDMA memory region */
	struct ibv_mr *mr = ibv_reg_mr(pd, rdma_dev->buf, length,
        IBV_ACCESS_LOCAL_WRITE |
        IBV_ACCESS_REMOTE_READ |
        IBV_ACCESS_REMOTE_WRITE);
    if (!mr) {
        printf("ibv_reg_mr failed\n");
        return NULL;
    }

	/* Save memory region information */
	rdma_dev->rbuf = (struct rdma_buffer *) 
		calloc(1, sizeof(struct rdma_buffer));
	if (!rdma_dev->rbuf) {
		printf("calloc failed\n");
		return NULL;
	}

    rdma_dev->rbuf->addr = (uint64_t) mr->addr;
    rdma_dev->rbuf->length = length;
    rdma_dev->rbuf->rkey = mr->lkey;

    return mr;
}

static int rdma_modify_qp(struct rdma_queue *q)
{
    struct ibv_qp_attr attr;

    memset(&attr, 0, sizeof(struct ibv_qp_attr));
    attr.pkey_index = 0;

    if (ibv_modify_qp(q->qp, &attr, IBV_QP_PKEY_INDEX)) {
        printf("ibv_modify_qp failed \n");
        return 1;
    }

    return 0;
}

static int on_addr_resolved(struct rdma_cm_id *id)
{
    struct rdma_queue *q = rdma_dev->queues[rdma_dev->queue_ctr];

    printf("%s\n", __func__);

	/* Set event->id->context to struct rdma_queue */
    id->context = q;

	/* Set queue pair id */
    q->cm_id = id;
	q->rdma_dev = rdma_dev;

	int ret = rdma_create_device(q);
	if (ret) {
		printf("rdma_create_device failed\n");
		return ret;
	}

	/* init RDMA queue pair */
    ret = rdma_create_queue(q, rdma_dev->cc[rdma_dev->queue_ctr++], true);
	if (ret) {
		printf("rdma_create_queue failed\n");
		goto close_device;
	}
    
	ret = rdma_modify_qp(q);
	if (ret) {
		printf("rdma_modify_qp failed\n");
		goto close_device;
	}

    ret = rdma_resolve_route(q->cm_id, CONNECTION_TIMEOUT_MS);
	if (ret) {
		printf("rdma_resolve_route failed\n");
		goto close_device;
	}

    return 0;

close_device:
	rdma_close_device();
	return ret;
}

static int on_route_resolved(struct rdma_queue *q)
{
    printf("%s\n", __func__);

	/* Send connection request */
    struct rdma_conn_param param = {};
    param.qp_num = q->qp->qp_num;
    param.initiator_depth = 16;
    param.responder_resources = 16;
    param.retry_count = 7;
    param.rnr_retry_count = 7;

    int ret = rdma_connect(q->cm_id, &param);
	if (ret) {
		printf("rdma_connect failed\n");
		return ret;
	}

    return 0;
}

static int on_connect_request(struct rdma_cm_id *id, 
		struct rdma_conn_param *param)
{
    struct rdma_queue *q = rdma_dev->queues[rdma_dev->queue_ctr];
	rdma_dev->queue_ctr++;

    printf("%s\n", __func__);

	/* Set event->id->context to struct rdma_queue */
    id->context = q;

	/* Set queue pair id */
    q->cm_id = id;
	q->rdma_dev = rdma_dev;

	int ret = rdma_create_device(q);
	if (ret) {
		printf("rdma_create_device failed\n");
		return ret;
	}

	/* Init RDMA queue pair */
    ret = rdma_create_queue(q, rdma_dev->cc[0], false);
	if (ret) {
		printf("rdma_create_queue failed\n");
		return ret;
	}

    struct ibv_device_attr attrs = {};
    ret = ibv_query_device(q->rdma_dev->verbs, &attrs);
	if (ret) {
		printf("ibv_query_device failed\n");
		return ret;
	}

	/* Accept connection request */
    struct rdma_conn_param cm_params = {};
    cm_params.initiator_depth = param->initiator_depth;
    cm_params.responder_resources = param->responder_resources;
    cm_params.rnr_retry_count = param->rnr_retry_count;
    cm_params.flow_control = param->flow_control;

    ret = rdma_accept(q->cm_id, &cm_params);
	if (ret) {
		printf("rdma_accept failed\n");
		return ret;
	}

    return 0;
}

static int on_connection(struct rdma_queue *q)
{
    printf("%s\n", __func__);
    return 1;
}

static int on_disconnect(struct rdma_queue *q)
{
    printf("%s\n", __func__);

    rdma_disconnect(q->cm_id);
    rdma_destroy_qp(q->cm_id);
    ibv_destroy_cq(q->cq);
    ibv_dealloc_pd(rdma_dev->pd);
	rdma_destroy_id(q->cm_id);
	for (int i = 0; i < NUM_QUEUES; i++)
		if (rdma_dev->ec[i])
		    rdma_destroy_event_channel(rdma_dev->ec[i]);
	free(rdma_dev);
    return 1;
}

static int on_event(struct rdma_cm_event *event)
{
    struct rdma_queue *q = (struct rdma_queue *) event->id->context;
    printf("%s\n", __func__);

    switch (event->event) {
        case RDMA_CM_EVENT_ADDR_RESOLVED:
            return on_addr_resolved(event->id);
        case RDMA_CM_EVENT_ROUTE_RESOLVED:
            return on_route_resolved(q);
        case RDMA_CM_EVENT_CONNECT_REQUEST:
            return on_connect_request(event->id, &event->param.conn);
        case RDMA_CM_EVENT_ESTABLISHED:
            return on_connection(q);
        case RDMA_CM_EVENT_DISCONNECTED:
            return on_disconnect(q);
        case RDMA_CM_EVENT_REJECTED:
            printf("connect rejected\n");
            return 1;
        default:
            printf("unknown event: %s\n", rdma_event_str(event->event));
            return 1;
    }
}

int rdma_open_server(struct sockaddr_in *s_addr, size_t length)
{
	int ret = rdma_open_device(true);
	if (ret) {
		printf("rdma_open_device failed\n");
		return ret;
	}

	/* Find RDMA device by addr */
	ret = rdma_bind_addr(rdma_dev->cm_id[0], (struct sockaddr *) s_addr);
	if (ret) {
		printf("rdma_bind_addr failed\n");
		return ret;
	}

	/* Listen client connection request */
	ret = rdma_listen(rdma_dev->cm_id[0], NUM_QUEUES + 1);
	if (ret) {
		printf("rdma_listen failed\n");
		return ret;
	}

    	struct rdma_cm_event *event;
    	for (int i = 0; i < NUM_QUEUES; i++) {
        	while (!rdma_get_cm_event(rdma_dev->ec[0], &event)) {
            	struct rdma_cm_event event_copy;

            	memcpy(&event_copy, event, sizeof(*event));
            	rdma_ack_cm_event(event);

            	if (on_event(&event_copy))
                	break;
        	}
    	}

	/* Create buffer for RDMA */
    	rdma_dev->mr = rdma_create_mr(rdma_dev->pd, length);
	if (!rdma_dev->mr) {
		printf("rdma_create_mr failed\n");
		return ret;
	}

	/* Successfully connected with server */
	rdma_dev->status = 1;
	while (!rdma_get_cm_event(rdma_dev->ec[0], &event)) {
		struct rdma_cm_event event_copy;

		memcpy(&event_copy, event, sizeof(*event));
		rdma_ack_cm_event(event);

		if (on_event(&event_copy))
			break;
	}

	return 0;
}

int rdma_open_client(struct sockaddr_in *s_addr, 
		struct sockaddr_in *c_addr, size_t length)
{
    int ret = rdma_open_device(false);
	if (ret) {
		printf("rdma_open_device failed\n");
		goto failed;
	}

    struct rdma_cm_event *event;
    for (unsigned int i = 0; i < NUM_QUEUES; i++) {
		ret = rdma_resolve_addr(rdma_dev->cm_id[i], 
				NULL, (struct sockaddr *) s_addr, CONNECTION_TIMEOUT_MS);
		if (ret) {
			printf("rdma_resolve_addr failed\n");
			return ret;
		}

		while (!rdma_get_cm_event(rdma_dev->ec[i], &event)) {
			struct rdma_cm_event event_copy;

			memcpy(&event_copy, event, sizeof(*event));
			rdma_ack_cm_event(event);

			if (on_event(&event_copy))
				break;
		}
	}

    rdma_dev->mr = rdma_create_mr(rdma_dev->pd, length);
	if (!rdma_dev->mr) {
		printf("rdma_create_mr failed\n");
		return ret;
	}

	/* Successfully connected with client */
	rdma_dev->status = 1;

	return 0;

failed:
	rdma_done();
	return ret;
}

struct rdma_queue *get_queue(int idx, int c)
{
	if (c) {
		return rdma_dev->queues[idx];
	} else {
		return rdma_dev->queues[idx];
	}
}

int rdma_is_connected(void)
{
	if (!rdma_dev)
		return 0;
	return rdma_dev->status;
}

void rdma_done(void)
{
	if (!rdma_dev)
		return;
	rdma_dev->status = -1;
}

int rdma_poll_cq(int c, int cpu, int total) {
	struct ibv_wc wc[total];
	struct rdma_queue *q = get_queue(cpu, c);
	int cnt = 0;
	int ret;

	while (cnt < total) {
		ret = ibv_poll_cq(q->cq, total, wc);
		cnt += ret;
	}

	if (cnt != total) {
		printf("ibv_poll_cq failed\n");
		return -1;
	}

	for (int i = 0 ; i < total; i++) {
		if (wc[i].status != IBV_WC_SUCCESS) {
			printf("[%d]: %s at %d\n", cpu, ibv_wc_status_str(wc[i].status), i);
			return -1;
		}
	}

	return 0;
}

int rdma_recv_wr(int c, int cpu, size_t length)
{
	struct rdma_queue *q = get_queue(cpu, c);

	struct ibv_sge sge = {};
	sge.addr = q->rdma_dev->rbuf->addr;
	sge.length = length;
	sge.lkey = q->rdma_dev->rbuf->rkey;

	struct ibv_recv_wr wr = {};
	wr.sg_list = &sge;
	wr.num_sge = 1;

	struct ibv_recv_wr *bad_wr;
	int ret = ibv_post_recv(q->qp, &wr, &bad_wr);
	if (ret) {
		printf("ibv_post_recv failed\n");
		return ret;
	}

	return 0;
}

int rdma_send_wr(int c, int cpu, size_t length)
{
	struct rdma_queue *q = get_queue(cpu, c);

	struct ibv_sge sge = {};
	sge.addr = q->rdma_dev->rbuf->addr;
	sge.length = length;
	sge.lkey = q->rdma_dev->rbuf->rkey;

	struct ibv_send_wr wr = {};
	wr.sg_list = &sge;
	wr.num_sge = 1;
	wr.opcode = IBV_WR_SEND;
	wr.send_flags = IBV_SEND_SIGNALED;
	/* Use this field with RDMA read/write
	if (wr_mr) {
		wr.wr.rdma.remote_addr = wr_mr->addr;
		wr.wr.rdma.rkey = wr_mr->stag.rkey;
	}
	*/

	struct ibv_send_wr *bad_wr;
	int ret = ibv_post_send(q->qp, &wr, &bad_wr);
	if (ret) {
		printf("ibv_post_send failed\n");
		return ret;
	}

	return 0;
}
