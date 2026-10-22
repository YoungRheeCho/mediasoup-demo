#include <stdint.h>
#include <string.h>
#include <arpa/inet.h>   

typedef struct {
    float latency;      // 4 B
    uint32_t FPS;      // 4 B
} qos_requirement;

typedef struct {
    uint32_t input_res_width;      // 4 B
    uint32_t input_res_height;      // 4 B
    uint32_t input_framerate;      // 4 B
    uint32_t input_codec;      // 4 B

    uint32_t input_res_width;      // 4 B
    uint32_t input_res_height;      // 4 B
    uint32_t input_framerate;      // 4 B
    uint32_t input_codec;      // 4 B
} app_feat;

typedef struct {
    uint32_t app_id;      // 4 B
    app_feat feat;
    qos_requirement qos;
} app_info;

typedef struct {
    uint32_t gpu_id;      // 4 B
    uint32_t enc_sessions;      // 4 B
    float enc_util;
    float dec_util;
} gpu_resource;

typedef struct {
    uint32_t node_id;      // 4 B
    float cpu_utilization;      // 4 B
    gpu_resource gpu;
    uint8_t node_ready;
    uint8_t isGPU;
    uint32_t pod_number;
} node_info;

typedef struct {
    uint8_t result;
} lb_signal;

typedef struct {
    uint32_t session_id;    
    uint32_t pid;    
    float avg_latency;     
    uint32_t FPS;     
} perf_info;