#ifndef RDMA_H
#define RDMA_H

#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <string.h>
#include <getopt.h>

#include <netdb.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <sys/socket.h>

#include <rdma/rdma_cma.h>
#include <infiniband/verbs.h>

#define true 1
#define false 0

#define NUM_QUEUES 4

#define CONNECTION_TIMEOUT_MS 2000
#define CQ_CAPACITY 128
#define MAX_SGE 1024
#define MAX_WR 1

#define PAGE_SIZE 4096

struct rdma_buffer {
    uint64_t addr;
    uint64_t length;
    uint32_t rkey;
};

struct rdma_queue {
    struct rdma_cm_id *cm_id; /* connection id (queue id) */
    struct ibv_qp *qp;
    struct ibv_cq *cq;

    struct rdma_device *rdma_dev;
};

struct rdma_device {
    struct rdma_event_channel *ec[NUM_QUEUES];
    struct rdma_cm_id *cm_id[NUM_QUEUES]; 
	struct ibv_comp_channel *cc[NUM_QUEUES];

    struct ibv_pd *pd;
    struct ibv_context *verbs;
	int status;

    struct rdma_queue *queues[NUM_QUEUES];
	int queue_ctr;

	char *buf;
	struct ibv_mr *mr;
    struct rdma_buffer *rbuf;
};

int rdma_open_server(struct sockaddr_in *, size_t);
int rdma_open_client(struct sockaddr_in *, struct sockaddr_in *, size_t);
void rdma_close_device(void);

int rdma_is_connected(void);
void rdma_done(void);

int rdma_poll_cq(int, int, int);
int rdma_recv_wr(int, int, size_t);
int rdma_send_wr(int, int, size_t);

struct rdma_queue *get_queue(int, int);

#endif
