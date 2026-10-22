import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv("/home/n2sl/yeon/qos/network/log/pacing/ace_bucket_log.csv")

# 실험 시작 기준 상대 시간(초)
df["t_sec"] = (df["time_ms"] - df["time_ms"].iloc[0]) / 1000.0

plt.figure(figsize=(10, 5))
plt.plot(df["t_sec"], df["bucket_size_bytes"], label="bucket")
plt.plot(df["t_sec"], df["predicted_queue_bytes"], label="queue")
plt.xlabel("Elapsed time (s)")
plt.ylabel("Bytes")
plt.title("Bucket / Queue over time")
plt.legend()
plt.grid(True)

plt.savefig("/home/n2sl/yeon/qos/network/log/pacing/ace_bucket_plot.png", dpi=200, bbox_inches="tight")
plt.close()